# Autowire

Autowire is a plug-and-play Python backend framework for APIs and WebSockets.

You create files inside a `routes/` folder. Autowire scans those files, finds
decorated functions, and wires the server automatically.

```text
routes/
  api.py
    users()        -> GET /users
    create_user()  -> POST /users when path="/users" is set
    stats()        -> WebSocket /stats
```

No manual router setup. No central route registry. Drop a function, add a
decorator, run the server.

## What Autowire Is For

Autowire is useful for:

- internal tools
- admin APIs
- realtime dashboards
- notification services
- bot backends
- IoT/device status servers
- prototypes that should still be deployable
- small SaaS APIs

The goal is simple: keep app code in route files and let Autowire handle the
boring wiring.

## Core Idea

Route files are just containers. Endpoint names come from function names unless
you explicitly set a path.

```python
from autowire import get, post, websocket


@get(auth=False)
def users(request):
    return {"message": "GET /users"}


@post("/users", auth=True)
def create_user(request):
    return {"message": "POST /users", "body": request.body}


@websocket("/chat", auth=False)
async def chat(socket):
    await socket.send("Connected")
```

This creates:

```text
GET  /users
POST /users
WS   /chat
```

Why explicit `"/users"` on `create_user`? Python cannot have two functions with
the same name in one file. So function names create paths by default, and
explicit paths let multiple methods share one endpoint.

## Features

- Function-name-based HTTP and WebSocket routes.
- Optional explicit route paths.
- Per-endpoint auth with `auth=True` or `auth=False`.
- `@get`, `@post`, `@put`, `@patch`, `@delete`, and `@websocket` decorators.
- ASGI server support through Uvicorn.
- JSON request parsing.
- JSON/plain-text response handling.
- SQLite helper with automatic database file creation.
- Optional endpoint-aware rate limiting.
- API token authentication.
- JWT authentication.
- Login/password endpoint that issues JWTs.
- Resolver callbacks for requester-owned API tokens and credentials.
- WebSocket auth through bearer headers or query tokens.
- Targeted WebSocket server pushes through a connection hub.

## Installation

Install from GitHub:

```bash
pip install git+https://github.com/chitransh819/autowire.git
```

For local development:

```bash
git clone https://github.com/chitransh819/autowire.git
cd autowire
pip install -e ".[dev]"
pytest
```

When published to PyPI later:

```bash
pip install autowire
```

## First Project

Create:

```text
my-api/
  routes/
    api.py
```

Create `routes/api.py`:

```python
import asyncio

from autowire import get, post, websocket, get_database

db = get_database()


async def ensure_schema():
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


@get("/users", auth=False)
async def list_users(request):
    await ensure_schema()
    users = await db.fetch_all(
        """
        SELECT id, name, created_at
        FROM users
        ORDER BY id ASC
        """
    )
    return {"users": users}


@post("/users", auth=True)
async def create_user(request):
    await ensure_schema()
    name = request.body["name"]
    user_id = await db.execute(
        "INSERT INTO users (name) VALUES (?)",
        (name,),
    )
    return {"id": user_id, "name": name}, 201


@websocket("/stats", auth=True)
async def user_stats(socket):
    while True:
        await ensure_schema()
        stats = await db.fetch_one(
            "SELECT COUNT(*) AS total_users FROM users"
        )
        await socket.send(stats or {"total_users": 0})
        await asyncio.sleep(15)
```

Run:

```bash
autowire run
```

Autowire starts at:

```text
http://127.0.0.1:8000
```

Detected routes:

```text
GET  /users
POST /users
WS   /stats
```

## CLI

Run from a project root:

```bash
autowire run
```

Choose a routes folder:

```bash
autowire run --routes app_routes
```

Change host/port:

```bash
autowire run --host 0.0.0.0 --port 8000
```

Enable rate limiting:

```bash
autowire run --rate-limit 120 --rate-period 60
```

## Route Paths

By default, function names become endpoint paths:

```python
from autowire import get


@get
def health(request):
    return {"ok": True}


@get
def user_stats(request):
    return {"active": 10}
```

Creates:

```text
GET /health
GET /user-stats
```

Use explicit paths when you want multiple functions on one endpoint:

```python
from autowire import get, post


@get("/users", auth=False)
def list_users(request):
    return {"users": []}


@post("/users", auth=True)
def create_user(request):
    return {"created": request.body}
```

Creates:

```text
GET  /users
POST /users
```

## Per-Endpoint Auth

Auth is decided per decorated function:

```python
from autowire import get, post, websocket


@get(auth=False)
def public_status(request):
    return {"ok": True}


@get(auth=True)
def account(request):
    return {"user": request.user}


@post("/users", auth=True)
def create_user(request):
    return {"created": True}


@websocket("/stats", auth=True)
async def stats(socket):
    await socket.send({"secure": True})
```

When auth is enabled, endpoints inherit `AuthConfig.default_required`. The
default is `True`, so endpoints are protected unless they set `auth=False`.

## Request Object

HTTP handlers receive `request`.

```python
@get("/debug", auth=True)
def debug(request):
    return {
        "method": request.method,
        "path": request.path,
        "query": request.query,
        "headers": request.headers,
        "user": request.user,
    }
```

Useful properties:

```python
request.method   # "GET", "POST", ...
request.path     # "/users"
request.headers  # lowercase dict
request.query    # dict[str, list[str]]
request.body     # parsed JSON dict, raw bytes, or {}
request.user     # authenticated user payload, or None
```

## Responses

```python
return {"ok": True}           # JSON 200
return {"created": True}, 201 # JSON 201
return "hello"                # plain text
return None                   # 204
```

## WebSockets

```python
from autowire import websocket


@websocket("/echo", auth=False)
async def echo(socket):
    await socket.send("Welcome")
    async for message in socket:
        await socket.send(f"Echo: {message}")
```

WebSocket streams work naturally:

```python
import asyncio

from autowire import websocket


@websocket("/stats", auth=True)
async def stats(socket):
    while True:
        await socket.send({"active": True})
        await asyncio.sleep(15)
```

### Targeted Server Pushes

WebSocket clients can send messages to the server, and the server can also push
messages back to one connected user. Autowire provides an in-process connection
hub for live delivery and a database-backed notification store for offline
delivery.

```python
from autowire import get_notification_store, post, websocket

notification_store = get_notification_store()


@websocket("/notifications", auth=True)
async def notification_stream(socket):
    user_id = socket.user["sub"]

    async with notification_store.connection(user_id, socket) as pending:
        await socket.send({
            "type": "connected",
            "pending_delivered": pending.delivered,
        })
        async for _message in socket:
            pass


@post("/notifications/send", auth=True)
async def send_notification(request):
    user_id = request.body["user_id"]
    payload = {
        "type": "notification",
        "message": request.body["message"],
    }
    result = await notification_store.send_to_user(user_id, payload)

    if not result.delivery.delivered:
        return {
            "sent": False,
            "stored": result.stored,
            "message": "User is not connected; notification stored for later",
            "delivery": result.as_dict(),
        }, 202

    return {
        "sent": True,
        "delivery": result.as_dict(),
    }
```

If the user is connected from multiple browser tabs/devices, the message is sent
to every active connection for that user. If the user is not connected, the
notification store saves the payload in SQLite and returns:

```python
{
    "delivery": {
        "delivered": False,
        "reason": "not_connected",
        ...
    },
    "stored": True,
    "notification_id": 1,
}
```

When that user opens `/notifications` later, `notification_store.connection(...)`
registers the WebSocket and flushes pending notifications automatically.

The built-in live connection hub is in-memory and works for a single running
server process. Pending notifications are stored in the configured Autowire
SQLite database. For multi-process or multi-server production deployments, keep
the same route code and put a shared broker such as Redis behind the live hub
later.

## Database

Autowire includes a small SQLite helper.

```python
from autowire import get_database

db = get_database()
```

Default path:

```text
data/autowire.db
```

Override it:

```powershell
$env:AUTOWIRE_DB_PATH = "data/my-app.db"
```

```bash
export AUTOWIRE_DB_PATH=data/my-app.db
```

The folder and database file are created automatically.

## Rate Limiting

```python
from autowire import RateLimit, ServerRateLimitConfig, create_app

app = create_app(
    "routes",
    rate_limit=ServerRateLimitConfig(
        default_limit=RateLimit(rate=120, period=60),
    ),
)
```

## Authentication Setup

Auth is configured once when creating the app. Individual endpoints decide
whether they require auth.

### API Tokens For Real Users

```python
from autowire import AuthConfig, create_app, get_database

db = get_database()


async def resolve_api_token(token: str):
    user = await db.fetch_one(
        """
        SELECT id, username, plan
        FROM users
        WHERE api_token = ?
        """,
        (token,),
    )
    if user is None:
        return None
    return {
        "sub": str(user["id"]),
        "username": user["username"],
        "plan": user["plan"],
    }


app = create_app(
    "routes",
    auth=AuthConfig(
        api_token_enabled=True,
        api_token_resolver=resolve_api_token,
    ),
)
```

Request:

```bash
curl http://localhost:8000/account -H "X-API-Token: user-token"
```

### Login And Password For Real Users

```python
from autowire import AuthConfig, create_app, get_database

db = get_database()


async def resolve_credentials(username: str, password: str):
    user = await db.fetch_one(
        """
        SELECT id, username, password_hash
        FROM users
        WHERE username = ?
        """,
        (username,),
    )
    if user is None:
        return None

    # Replace this with bcrypt/argon2 hash verification in production.
    if user["password_hash"] != password:
        return None

    return {
        "sub": str(user["id"]),
        "username": user["username"],
    }


app = create_app(
    "routes",
    auth=AuthConfig(
        login_enabled=True,
        credential_resolver=resolve_credentials,
        jwt_secret="change-this-secret",
    ),
)
```

Autowire automatically creates:

```text
POST /auth/login
```

Login:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"alice\",\"password\":\"alice-password\"}"
```

Use the returned JWT:

```bash
curl http://localhost:8000/account \
  -H "Authorization: Bearer <jwt>"
```

### JWT Verification

If another service already issues JWTs:

```python
app = create_app(
    "routes",
    auth=AuthConfig(
        jwt_enabled=True,
        jwt_secret="change-this-secret",
    ),
)
```

### WebSocket Auth

WebSockets can authenticate with:

```text
Authorization: Bearer <jwt>
```

or:

```text
ws://localhost:8000/stats?token=<jwt>
```

## Production Entrypoint

Create `app.py`:

```python
import os

from autowire import AuthConfig, RateLimit, ServerRateLimitConfig, create_app

app = create_app(
    "routes",
    rate_limit=ServerRateLimitConfig(
        default_limit=RateLimit(
            rate=int(os.getenv("RATE_LIMIT", "120")),
            period=float(os.getenv("RATE_PERIOD", "60")),
        ),
    ),
    auth=AuthConfig(
        jwt_enabled=os.getenv("JWT_ENABLED") == "true",
        jwt_secret=os.getenv("JWT_SECRET", ""),
    ),
)
```

Run:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

For production, run behind a reverse proxy such as Nginx/Caddy and use a process
manager such as systemd, Docker, or your hosting provider's service manager.

## Supporting Packages

Autowire can later integrate with:

- `ws-reconnect-manager` for reconnecting client-side WebSocket connections.
- `smart-api-limiter` or another server rate-limiter package for external
  rate-limit implementations.

Autowire currently includes small built-in compatible pieces so it works before
those packages are published.

## Repository Structure

```text
autowire/
  autowire/          # framework source
  examples/routes/   # minimal examples
  tests/             # framework tests
  pyproject.toml
  README.md
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
