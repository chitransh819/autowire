from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from autowire import AuthConfig, create_app


@pytest.fixture
def workspace_tmp() -> Path:
    root = Path("test-workspace")
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


async def call_http(
    app: Any,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "client": ("127.0.0.1", 12345),
            "headers": headers or [],
            "query_string": b"",
        },
        receive,
        send,
    )
    return messages


async def call_websocket(
    app: Any,
    *,
    path: str,
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "websocket",
            "path": path,
            "query_string": query_string,
            "headers": headers or [],
        },
        receive,
        send,
    )
    return messages


def status(messages: list[dict[str, Any]]) -> int:
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def body(messages: list[dict[str, Any]]) -> Any:
    raw = next(message["body"] for message in messages if message["type"] == "http.response.body")
    return json.loads(raw.decode())


def make_routes(tmp_path: Path) -> Path:
    routes = tmp_path / "routes"
    routes.mkdir()
    (routes / "users.py").write_text(
        "from autowire import get\n\n@get\ndef fetch(request):\n    return {'user': request.user}\n",
        encoding="utf-8",
    )
    (routes / "stats.py").write_text(
        "from autowire import websocket\n\n"
        "@websocket\n"
        "async def connect(socket):\n"
        "    await socket.send({'ok': True})\n",
        encoding="utf-8",
    )
    return routes


@pytest.mark.asyncio
async def test_api_token_auth_protects_http_routes(workspace_tmp: Path) -> None:
    app = create_app(
        make_routes(workspace_tmp),
        auth=AuthConfig(api_token_enabled=True, api_tokens=frozenset({"secret-token"})),
    )

    rejected = await call_http(app, method="GET", path="/users")
    accepted = await call_http(
        app,
        method="GET",
        path="/users",
        headers=[(b"x-api-token", b"secret-token")],
    )

    assert status(rejected) == 401
    assert status(accepted) == 200
    assert body(accepted)["user"]["type"] == "api_token"


@pytest.mark.asyncio
async def test_api_token_resolver_supports_many_requesters(workspace_tmp: Path) -> None:
    async def resolve_token(token: str) -> dict[str, Any] | None:
        users = {
            "alice-token": {"sub": "alice", "plan": "pro"},
            "bob-token": {"sub": "bob", "plan": "free"},
        }
        return users.get(token)

    app = create_app(
        make_routes(workspace_tmp),
        auth=AuthConfig(api_token_enabled=True, api_token_resolver=resolve_token),
    )

    alice = await call_http(
        app,
        method="GET",
        path="/users",
        headers=[(b"x-api-token", b"alice-token")],
    )
    bob = await call_http(
        app,
        method="GET",
        path="/users",
        headers=[(b"x-api-token", b"bob-token")],
    )

    assert body(alice)["user"]["sub"] == "alice"
    assert body(bob)["user"]["sub"] == "bob"


@pytest.mark.asyncio
async def test_login_issues_jwt_that_can_access_routes(workspace_tmp: Path) -> None:
    app = create_app(
        make_routes(workspace_tmp),
        auth=AuthConfig(
            jwt_secret="test-secret",
            login_enabled=True,
            login_username="admin",
            login_password="password",
        ),
    )

    login = await call_http(
        app,
        method="POST",
        path="/auth/login",
        body=b'{"username":"admin","password":"password"}',
        headers=[(b"content-type", b"application/json")],
    )
    token = body(login)["access_token"]
    accepted = await call_http(
        app,
        method="GET",
        path="/users",
        headers=[(b"authorization", f"Bearer {token}".encode())],
    )

    assert status(login) == 200
    assert status(accepted) == 200
    assert body(accepted)["user"]["sub"] == "admin"


@pytest.mark.asyncio
async def test_credential_resolver_supports_many_logins(workspace_tmp: Path) -> None:
    async def resolve_credentials(username: str, password: str) -> dict[str, Any] | None:
        users = {
            "alice": {"password": "alice-pass", "id": "user-1"},
            "bob": {"password": "bob-pass", "id": "user-2"},
        }
        user = users.get(username)
        if user is None or user["password"] != password:
            return None
        return {"sub": username, "id": user["id"]}

    app = create_app(
        make_routes(workspace_tmp),
        auth=AuthConfig(
            login_enabled=True,
            jwt_secret="test-secret",
            credential_resolver=resolve_credentials,
        ),
    )

    login = await call_http(
        app,
        method="POST",
        path="/auth/login",
        body=b'{"username":"alice","password":"alice-pass"}',
        headers=[(b"content-type", b"application/json")],
    )
    token = body(login)["access_token"]
    accepted = await call_http(
        app,
        method="GET",
        path="/users",
        headers=[(b"authorization", f"Bearer {token}".encode())],
    )

    assert status(login) == 200
    assert body(accepted)["user"]["sub"] == "alice"
    assert body(accepted)["user"]["id"] == "user-1"


@pytest.mark.asyncio
async def test_websocket_auth_accepts_query_token(workspace_tmp: Path) -> None:
    app = create_app(
        make_routes(workspace_tmp),
        auth=AuthConfig(
            jwt_secret="test-secret",
            login_enabled=True,
            login_username="admin",
            login_password="password",
        ),
    )
    login = await call_http(
        app,
        method="POST",
        path="/auth/login",
        body=b'{"username":"admin","password":"password"}',
        headers=[(b"content-type", b"application/json")],
    )
    token = body(login)["access_token"].encode()

    rejected = await call_websocket(app, path="/stats")
    accepted = await call_websocket(app, path="/stats", query_string=b"token=" + token)

    assert rejected == [{"type": "websocket.close", "code": 1008}]
    assert accepted[0] == {"type": "websocket.accept"}
