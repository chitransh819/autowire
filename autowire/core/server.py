"""Minimal ASGI server primitives for Autowire."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from ..auth import AuthConfig, AuthMiddleware, create_login_endpoint
from .loader import scan_routes
from .rate_limiter import ASGIRateLimitMiddleware, ServerRateLimitConfig
from .router import wire

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
Endpoint = Callable[..., Any]


@dataclass(slots=True)
class Request:
    scope: Scope
    body: Any
    raw_body: bytes

    @property
    def method(self) -> str:
        return str(self.scope.get("method", "GET")).upper()

    @property
    def path(self) -> str:
        return str(self.scope.get("path", "/"))

    @property
    def headers(self) -> dict[str, str]:
        return {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in self.scope.get("headers", [])
        }

    @property
    def query(self) -> dict[str, list[str]]:
        raw = self.scope.get("query_string", b"")
        return parse_qs(raw.decode("latin-1"))

    @property
    def user(self) -> dict[str, Any] | None:
        user = self.scope.get("autowire.user")
        return user if isinstance(user, dict) else None


class WebSocket:
    def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scope = scope
        self._receive = receive
        self._send = send
        self.accepted = False

    async def accept(self) -> None:
        if not self.accepted:
            await self._send({"type": "websocket.accept"})
            self.accepted = True

    async def send(self, data: Any) -> None:
        await self.accept()
        if isinstance(data, bytes):
            await self._send({"type": "websocket.send", "bytes": data})
        elif isinstance(data, str):
            await self._send({"type": "websocket.send", "text": data})
        else:
            await self._send({"type": "websocket.send", "text": json.dumps(data)})

    async def close(self, code: int = 1000) -> None:
        await self._send({"type": "websocket.close", "code": code})

    def __aiter__(self) -> "WebSocket":
        return self

    async def __anext__(self) -> str | bytes:
        await self.accept()
        while True:
            message = await self._receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                raise StopAsyncIteration
            if message_type != "websocket.receive":
                continue
            if message.get("text") is not None:
                return message["text"]
            if message.get("bytes") is not None:
                return message["bytes"]


class AutoWireApp:
    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Endpoint] = {}
        self.route_names: dict[tuple[str, str], str] = {}
        self.websockets: dict[str, Endpoint] = {}
        self.websocket_names: dict[str, str] = {}

    def add_route(self, path: str, endpoint: Endpoint, method: str, *, name: str | None = None) -> None:
        key = (method.upper(), _normalize_path(path))
        self.routes[key] = endpoint
        self.route_names[key] = name or endpoint.__name__

    def add_websocket(self, path: str, endpoint: Endpoint, *, name: str | None = None) -> None:
        path = _normalize_path(path)
        self.websockets[path] = endpoint
        self.websocket_names[path] = name or endpoint.__name__

    def describe_routes(self) -> list[str]:
        http = [f"{method} {path}" for (method, path) in sorted(self.routes)]
        websockets = [f"WS {path}" for path in sorted(self.websockets)]
        return [*http, *websockets]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type == "http":
            await self._handle_http(scope, receive, send)
            return
        if scope_type == "websocket":
            await self._handle_websocket(scope, receive, send)
            return
        raise RuntimeError(f"unsupported ASGI scope type: {scope_type}")

    async def _handle_http(self, scope: Scope, receive: Receive, send: Send) -> None:
        method = str(scope.get("method", "GET")).upper()
        path = _normalize_path(str(scope.get("path", "/")))
        endpoint = self.routes.get((method, path))
        if endpoint is None:
            await _json_response(send, {"detail": "Not found"}, status=404)
            return

        request = Request(scope=scope, body=None, raw_body=await _read_body(receive))
        request.body = _parse_body(request.raw_body, request.headers)
        try:
            result = endpoint(request)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            await _json_response(send, {"detail": str(exc)}, status=500)
            return
        await _send_result(send, result)

    async def _handle_websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = _normalize_path(str(scope.get("path", "/")))
        endpoint = self.websockets.get(path)
        if endpoint is None:
            await send({"type": "websocket.close", "code": 1008})
            return
        socket = WebSocket(scope, receive, send)
        try:
            result = endpoint(socket)
            if inspect.isawaitable(result):
                await result
        except Exception:
            await socket.close(code=1011)


def create_app(
    routes_folder: str | Path = "routes",
    *,
    rate_limit: ServerRateLimitConfig | None = None,
    auth: AuthConfig | None = None,
) -> AutoWireApp | AuthMiddleware | ASGIRateLimitMiddleware:
    app = AutoWireApp()
    routes = scan_routes(routes_folder)
    wire(app, routes)
    wrapped: AutoWireApp | AuthMiddleware | ASGIRateLimitMiddleware = app
    if auth is not None and auth.login_enabled:
        app.add_route("/auth/login", create_login_endpoint(auth), "POST", name="login")
    if auth is not None and auth.enabled:
        wrapped = AuthMiddleware(wrapped, auth)
    if rate_limit is not None:
        wrapped = ASGIRateLimitMiddleware(wrapped, rate_limit)
    return wrapped


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            continue
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks)


def _parse_body(raw_body: bytes, headers: Mapping[str, str]) -> Any:
    if not raw_body:
        return {}
    content_type = headers.get("content-type", "")
    if "application/json" in content_type:
        return json.loads(raw_body.decode("utf-8"))
    return raw_body


async def _send_result(send: Send, result: Any) -> None:
    if result is None:
        await _empty_response(send)
    elif isinstance(result, tuple) and len(result) == 2:
        body, status = result
        await _json_response(send, body, status=status)
    elif isinstance(result, (bytes, str)):
        await _plain_response(send, result)
    else:
        await _json_response(send, result)


async def _json_response(send: Send, body: Any, *, status: int = 200) -> None:
    payload = json.dumps(body).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _plain_response(send: Send, body: bytes | str, *, status: int = 200) -> None:
    payload = body if isinstance(body, bytes) else body.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(payload)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _empty_response(send: Send) -> None:
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


def _normalize_path(path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") or "/"
