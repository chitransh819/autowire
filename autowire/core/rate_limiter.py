"""Endpoint-aware ASGI rate limiting for Autowire.

This follows the updated server-side ``smart-api-limiter`` API while keeping
Autowire's initial compatibility names.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from math import ceil
from time import monotonic
from typing import Any

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
KeyFunc = Callable[[Scope], str]
RuleFunc = Callable[[Scope], "RateLimit"]
CostFunc = Callable[[Scope], int]


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A token bucket rate limit."""

    rate: int
    period: float
    burst: int | None = None

    def __post_init__(self) -> None:
        if self.rate < 1:
            raise ValueError("rate must be >= 1")
        if self.period <= 0:
            raise ValueError("period must be > 0")
        if self.burst is not None and self.burst < 1:
            raise ValueError("burst must be >= 1")

    @property
    def capacity(self) -> int:
        return self.burst or self.rate

    @property
    def refill_per_second(self) -> float:
        return self.rate / self.period


RateLimitConfig = RateLimit


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: float
    reset_after: float


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class InMemoryRateLimiter:
    """Concurrency-safe in-memory token bucket limiter."""

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def check(
        self,
        key: str,
        limit: RateLimit,
        *,
        cost: int = 1,
    ) -> RateLimitDecision:
        if cost < 1:
            raise ValueError("cost must be >= 1")
        if cost > limit.capacity:
            raise ValueError("cost cannot exceed the bucket capacity")

        now = monotonic()
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(limit.capacity), updated_at=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(
                float(limit.capacity),
                bucket.tokens + elapsed * limit.refill_per_second,
            )
            bucket.updated_at = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return RateLimitDecision(
                    allowed=True,
                    limit=limit.capacity,
                    remaining=int(bucket.tokens),
                    retry_after=0.0,
                    reset_after=_reset_after(bucket.tokens, limit),
                )

            missing = cost - bucket.tokens
            retry_after = missing / limit.refill_per_second
            return RateLimitDecision(
                allowed=False,
                limit=limit.capacity,
                remaining=0,
                retry_after=retry_after,
                reset_after=retry_after,
            )

    async def reset(self, key: str | None = None) -> None:
        async with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    async def cleanup(self, max_idle_seconds: float) -> int:
        now = monotonic()
        async with self._lock:
            stale = [
                key
                for key, bucket in self._buckets.items()
                if now - bucket.updated_at >= max_idle_seconds
            ]
            for key in stale:
                self._buckets.pop(key, None)
            return len(stale)


@dataclass(frozen=True, slots=True)
class ServerRateLimitConfig:
    default_limit: RateLimit = RateLimit(rate=60, period=60)
    limiter: InMemoryRateLimiter | None = None
    key_for: KeyFunc | None = None
    rule_for: RuleFunc | None = None
    cost_for: CostFunc | None = None
    exempt_paths: frozenset[str] = frozenset()

    def __init__(
        self,
        default_limit: RateLimit | None = None,
        *,
        rate_limit: RateLimit | None = None,
        limiter: InMemoryRateLimiter | None = None,
        key_for: KeyFunc | None = None,
        key_func: KeyFunc | None = None,
        rule_for: RuleFunc | None = None,
        cost_for: CostFunc | None = None,
        exempt_paths: set[str] | frozenset[str] | tuple[str, ...] = (),
    ) -> None:
        object.__setattr__(self, "default_limit", default_limit or rate_limit or RateLimit(60, 60))
        object.__setattr__(self, "limiter", limiter)
        object.__setattr__(self, "key_for", key_for or key_func)
        object.__setattr__(self, "rule_for", rule_for)
        object.__setattr__(self, "cost_for", cost_for)
        object.__setattr__(self, "exempt_paths", frozenset(exempt_paths))


class RateLimitMiddleware:
    """ASGI middleware that rejects over-limit requests with HTTP 429."""

    def __init__(
        self,
        app: ASGIApp,
        config: ServerRateLimitConfig | None = None,
        *,
        limiter: InMemoryRateLimiter | None = None,
        default_limit: RateLimit | None = None,
        key_for: KeyFunc | None = None,
        rule_for: RuleFunc | None = None,
        cost_for: CostFunc | None = None,
        exempt_paths: set[str] | frozenset[str] | tuple[str, ...] = (),
    ) -> None:
        config = config or ServerRateLimitConfig(
            default_limit=default_limit,
            limiter=limiter,
            key_for=key_for,
            rule_for=rule_for,
            cost_for=cost_for,
            exempt_paths=exempt_paths,
        )
        self.app = app
        self.limiter = config.limiter or InMemoryRateLimiter()
        self.default_limit = config.default_limit
        self.key_for = config.key_for or default_key_for
        self.rule_for = config.rule_for or (lambda scope: self.default_limit)
        self.cost_for = config.cost_for or (lambda scope: 1)
        self.exempt_paths = config.exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        key = self.key_for(scope)
        rule = self.rule_for(scope)
        cost = self.cost_for(scope)
        bucket_key = f"{key}:{scope.get('method', 'GET')}:{scope.get('path', '/')}"
        decision = await self.limiter.check(bucket_key, rule, cost=cost)
        if not decision.allowed:
            await _send_limited(send, decision)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.extend(_limit_headers(decision, include_retry_after=False))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


ASGIRateLimitMiddleware = RateLimitMiddleware


def default_key_for(scope: Scope) -> str:
    headers = dict(scope.get("headers") or [])
    api_key = headers.get(b"x-api-key") or headers.get(b"authorization")
    if api_key:
        return api_key.decode(errors="ignore")
    client = scope.get("client")
    if client:
        return str(client[0])
    return "anonymous"


default_key_func = default_key_for


def json_limit_body(message: str = "Rate limit exceeded") -> bytes:
    return json.dumps({"detail": message}).encode("utf-8")


async def _send_limited(send: Send, decision: RateLimitDecision) -> None:
    body = json.dumps(
        {
            "detail": "rate limit exceeded",
            "retry_after": ceil(decision.retry_after),
        }
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *_limit_headers(decision, include_retry_after=True),
    ]
    await send(
        {
            "type": "http.response.start",
            "status": 429,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


def _limit_headers(
    decision: RateLimitDecision,
    *,
    include_retry_after: bool,
) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-ratelimit-limit", str(decision.limit).encode()),
        (b"x-ratelimit-remaining", str(decision.remaining).encode()),
        (b"x-ratelimit-reset", str(ceil(decision.reset_after)).encode()),
    ]
    if include_retry_after:
        headers.append((b"retry-after", str(ceil(decision.retry_after)).encode()))
    return headers


def _reset_after(tokens: float, limit: RateLimit) -> float:
    missing = max(0.0, limit.capacity - tokens)
    return missing / limit.refill_per_second

