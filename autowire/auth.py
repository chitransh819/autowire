"""Configurable API token and JWT authentication for Autowire."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import inspect
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from dataclasses import dataclass
from typing import Any

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
UserPayload = dict[str, Any]
APITokenResolver = Callable[[str], UserPayload | None | Awaitable[UserPayload | None]]
CredentialResolver = Callable[[str, str], UserPayload | None | Awaitable[UserPayload | None]]


@dataclass(frozen=True, slots=True)
class AuthConfig:
    api_token_enabled: bool = False
    api_tokens: frozenset[str] = frozenset()
    api_token_resolver: APITokenResolver | None = None
    jwt_enabled: bool = False
    jwt_secret: str = ""
    login_enabled: bool = False
    login_username: str = ""
    login_password: str = ""
    credential_resolver: CredentialResolver | None = None
    token_ttl_seconds: int = 3600
    exempt_paths: frozenset[str] = frozenset({"/health", "/auth/login"})

    @property
    def enabled(self) -> bool:
        return self.api_token_enabled or self.jwt_enabled or self.login_enabled

    def validate(self) -> None:
        if self.api_token_enabled and not self.api_tokens and self.api_token_resolver is None:
            raise ValueError("api token auth requires api_tokens or api_token_resolver")
        if (self.jwt_enabled or self.login_enabled) and not self.jwt_secret:
            raise ValueError("JWT/login auth requires a jwt_secret")
        if (
            self.login_enabled
            and self.credential_resolver is None
            and (not self.login_username or not self.login_password)
        ):
            raise ValueError(
                "login auth requires login_username/login_password or credential_resolver"
            )


class AuthMiddleware:
    def __init__(self, app: ASGIApp, config: AuthConfig) -> None:
        config.validate()
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.config.enabled:
            await self.app(scope, receive, send)
            return

        scope_type = scope.get("type")
        path = str(scope.get("path", "/"))
        if path in self.config.exempt_paths:
            await self.app(scope, receive, send)
            return

        user = await authenticate_scope(scope, self.config)
        if user is None:
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await _json_response(send, {"detail": "Unauthorized"}, status=401)
            return

        scope["autowire.user"] = user
        await self.app(scope, receive, send)


async def authenticate_scope(scope: Scope, config: AuthConfig) -> dict[str, Any] | None:
    headers = _headers(scope)
    if config.api_token_enabled:
        token = headers.get("x-api-token") or _bearer_token(headers.get("authorization"))
        if token is not None:
            resolved = await _resolve_api_token(token, config)
            if resolved is not None:
                return {"type": "api_token", **resolved}

    if config.jwt_enabled or config.login_enabled:
        token = _bearer_token(headers.get("authorization")) or _query_token(scope)
        if token is not None:
            payload = verify_jwt(token, config.jwt_secret)
            if payload is not None:
                return {"type": "jwt", **payload}
    return None


def create_login_endpoint(config: AuthConfig) -> Callable[..., Any]:
    async def login(request: Any) -> tuple[dict[str, Any], int]:
        username = request.body.get("username") if isinstance(request.body, dict) else None
        password = request.body.get("password") if isinstance(request.body, dict) else None
        user = await _resolve_credentials(str(username or ""), str(password or ""), config)
        if user is None:
            return {"detail": "Invalid credentials"}, 401

        now = int(time.time())
        subject = str(user.get("sub") or user.get("id") or user.get("username") or username)
        token = create_jwt(
            {
                **user,
                "sub": subject,
                "iat": now,
                "exp": now + config.token_ttl_seconds,
            },
            config.jwt_secret,
        )
        return {"access_token": token, "token_type": "bearer"}, 200

    return login


async def _resolve_api_token(token: str, config: AuthConfig) -> dict[str, Any] | None:
    if config.api_token_resolver is not None:
        resolved = config.api_token_resolver(token)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        return dict(resolved) if resolved is not None else None
    if token in config.api_tokens:
        return {"token": token}
    return None


async def _resolve_credentials(
    username: str,
    password: str,
    config: AuthConfig,
) -> dict[str, Any] | None:
    if config.credential_resolver is not None:
        resolved = config.credential_resolver(username, password)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        return dict(resolved) if resolved is not None else None
    if not _constant_time_equal(username, config.login_username):
        return None
    if not _constant_time_equal(password, config.login_password):
        return None
    return {"username": config.login_username}


def create_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    encoded_header = _b64_json(header)
    encoded_payload = _b64_json(payload)
    signing_input = f"{encoded_header}.{encoded_payload}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64_encode(signature)}"


def verify_jwt(token: str, secret: str) -> dict[str, Any] | None:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".", 2)
        header = json.loads(_b64_decode(encoded_header))
        if header.get("alg") != "HS256":
            return None
        signing_input = f"{encoded_header}.{encoded_payload}".encode()
        expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        actual = _b64_decode(encoded_signature)
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(_b64_decode(encoded_payload))
    except (ValueError, json.JSONDecodeError):
        return None

    exp = payload.get("exp")
    if exp is not None and int(exp) < int(time.time()):
        return None
    return payload


def parse_api_tokens(raw: str | Iterable[str]) -> frozenset[str]:
    if isinstance(raw, str):
        return frozenset(token.strip() for token in raw.split(",") if token.strip())
    return frozenset(token for token in raw if token)


def _headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _query_token(scope: Scope) -> str | None:
    raw = scope.get("query_string", b"")
    for part in raw.decode("latin-1").split("&"):
        key, _, value = part.partition("=")
        if key in {"token", "access_token"} and value:
            return value
    return None


def _b64_json(data: dict[str, Any]) -> str:
    return _b64_encode(json.dumps(data, separators=(",", ":")).encode())


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


async def _json_response(send: Send, body: Any, *, status: int) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
