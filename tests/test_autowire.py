from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from autowire import RateLimit, RateLimitConfig, ServerRateLimitConfig, create_app
from autowire.core.server import AutoWireApp


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


def response_status(messages: list[dict[str, Any]]) -> int:
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def response_body(messages: list[dict[str, Any]]) -> Any:
    raw = next(message["body"] for message in messages if message["type"] == "http.response.body")
    return json.loads(raw.decode("utf-8"))


def test_create_app_scans_route_files(workspace_tmp: Path) -> None:
    routes = workspace_tmp / "routes"
    routes.mkdir()
    (routes / "api.py").write_text(
        "from autowire import get\n\n@get\ndef users(request):\n    return {'users': ['Alice']}\n",
        encoding="utf-8",
    )

    app = create_app(routes)

    assert isinstance(app, AutoWireApp)
    assert app.describe_routes() == ["GET /users"]


@pytest.mark.asyncio
async def test_http_route_returns_json(workspace_tmp: Path) -> None:
    routes = workspace_tmp / "routes"
    routes.mkdir()
    (routes / "api.py").write_text(
        "from autowire import get\n\n@get\ndef users(request):\n    return {'users': ['Alice']}\n",
        encoding="utf-8",
    )
    app = create_app(routes)

    messages = await call_http(app, method="GET", path="/users")

    assert response_status(messages) == 200
    assert response_body(messages) == {"users": ["Alice"]}


@pytest.mark.asyncio
async def test_post_route_receives_json_body(workspace_tmp: Path) -> None:
    routes = workspace_tmp / "routes"
    routes.mkdir()
    (routes / "api.py").write_text(
        "from autowire import post\n\n"
        "@post('/users')\n"
        "def create(request):\n"
        "    return {'created': request.body['name']}\n",
        encoding="utf-8",
    )
    app = create_app(routes)

    messages = await call_http(
        app,
        method="POST",
        path="/users",
        body=b'{"name":"Chitransh"}',
        headers=[(b"content-type", b"application/json")],
    )

    assert response_status(messages) == 200
    assert response_body(messages) == {"created": "Chitransh"}


@pytest.mark.asyncio
async def test_rate_limit_can_wrap_autowire_app(workspace_tmp: Path) -> None:
    routes = workspace_tmp / "routes"
    routes.mkdir()
    (routes / "api.py").write_text(
        "from autowire import get\n\n@get\ndef users(request):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    app = create_app(
        routes,
        rate_limit=ServerRateLimitConfig(rate_limit=RateLimitConfig(rate=1, period=60)),
    )

    first = await call_http(app, method="GET", path="/users")
    second = await call_http(app, method="GET", path="/users")

    assert response_status(first) == 200
    assert response_status(second) == 429


@pytest.mark.asyncio
async def test_rate_limit_tracks_endpoints_independently(workspace_tmp: Path) -> None:
    routes = workspace_tmp / "routes"
    routes.mkdir()
    (routes / "api.py").write_text(
        "from autowire import get\n\n"
        "@get\n"
        "def users(request):\n"
        "    return {'users': []}\n\n"
        "@get\n"
        "def orders(request):\n"
        "    return {'orders': []}\n",
        encoding="utf-8",
    )
    app = create_app(
        routes,
        rate_limit=ServerRateLimitConfig(default_limit=RateLimit(rate=1, period=60)),
    )

    users = await call_http(app, method="GET", path="/users")
    orders = await call_http(app, method="GET", path="/orders")

    assert response_status(users) == 200
    assert response_status(orders) == 200


@pytest.mark.asyncio
async def test_successful_rate_limited_responses_do_not_emit_retry_after(
    workspace_tmp: Path,
) -> None:
    routes = workspace_tmp / "routes"
    routes.mkdir()
    (routes / "api.py").write_text(
        "from autowire import get\n\n@get\ndef users(request):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    app = create_app(
        routes,
        rate_limit=ServerRateLimitConfig(default_limit=RateLimitConfig(rate=2, period=60)),
    )

    messages = await call_http(app, method="GET", path="/users")
    start = next(message for message in messages if message["type"] == "http.response.start")

    assert response_status(messages) == 200
    assert (b"retry-after", b"0") not in start["headers"]


@pytest.mark.asyncio
async def test_explicit_path_allows_multiple_methods_on_same_endpoint(
    workspace_tmp: Path,
) -> None:
    routes = workspace_tmp / "routes"
    routes.mkdir()
    (routes / "api.py").write_text(
        "from autowire import get, post\n\n"
        "@get('/users')\n"
        "def list_users(request):\n"
        "    return {'method': 'get'}\n\n"
        "@post('/users')\n"
        "def create_user(request):\n"
        "    return {'method': 'post'}\n",
        encoding="utf-8",
    )
    app = create_app(routes)

    get_messages = await call_http(app, method="GET", path="/users")
    post_messages = await call_http(app, method="POST", path="/users")

    assert response_body(get_messages) == {"method": "get"}
    assert response_body(post_messages) == {"method": "post"}
