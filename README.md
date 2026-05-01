# Autowire

Autowire is a plug-and-play Python backend framework for building APIs and
WebSocket services with almost no setup.

Instead of manually creating routers, importing route modules, and registering
every endpoint, you create files inside a `routes/` folder. Autowire scans those
files and wires the server automatically.

```text
routes/
  users.py       -> GET /users, POST /users
  orders.py      -> GET /orders, POST /orders
  stats.py       -> WebSocket /stats
  chat.py        -> WebSocket /chat
```

Drop a file. Add a decorator. Run the server.

## What Autowire Is For

Autowire is useful when you want to build a backend quickly but still keep a
real server-side structure:

- internal tools
- dashboards
- realtime status panels
- small SaaS APIs
- admin APIs
- bot backends
- IoT/device status servers
- notification services
- prototypes that should still be deployable later

It is intentionally simple: route files contain your application logic, and
Autowire handles the wiring.

## Core Idea

A route file becomes an endpoint path.

```text
routes/users.py       -> /users
routes/user_stats.py  -> /user-stats
routes/chat.py        -> /chat
```

Decorators decide what type of endpoint exists in that file.

```python
from autowire import get, post, websocket


@get
def fetch(request):
    return {"message": "GET /users"}


@post
def create(request):
    return {"message": "POST /users", "body": request.body}


@websocket
async def connect(socket):
    await socket.send("Connected")
```

If this code is in `routes/users.py`, Autowire creates:

```text
GET /users
POST /users
WS  /users
```

## Features

- File-based HTTP routing.
- File-based WebSocket routing.
- `@get`, `@post`, `@put`, `@patch`, `@delete`, and `@websocket` decorators.
- ASGI server support through Uvicorn.
- JSON request parsing.
- JSON/plain-text response handling.
- SQLite helper with automatic database file creation.
- Optional endpoint-aware rate limiting.
- Optional API token authentication.
- Optional JWT authentication.
- Optional login/password endpoint that issues JWTs.
- Resolver callbacks so each requester can use their own API token or login.
- WebSocket auth through bearer headers or query tokens.
- Small client-side WebSocket adapter hook for `ws-reconnect-manager`.

## Installation

From GitHub:

```bash
pip install git+https://github.com/chitransh819/autowire.git
```

For local development from source:

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

Create a new folder:

```text
my-api/
  routes/
    users.py
    stats.py
```

Create `routes/users.py`:

```python
from autowire import get, post, get_database

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


@get
async def fetch(request):
    await ensure_schema()
    users = await db.fetch_all(
        """
        SELECT id, name, created_at
        FROM users
        ORDER BY id ASC
        """
    )
    return {"users": users}


@post
async def create(request):
    await ensure_schema()
    name = request.body["name"]
    user_id = await db.execute(
        "INSERT INTO users (name) VALUES (?)",
        (name,),
    )
    return {"id": user_id, "name": name}, 201
```

Create `routes/stats.py`:

```python
import asyncio

from autowire import get_database, websocket

db = get_database()


@websocket
async def connect(socket):
    while True:
        stats = await db.fetch_one(
            "SELECT COUNT(*) AS total_users FROM users"
        )
        await socket.send(stats or {"total_users": 0})
        await asyncio.sleep(15)
```

Run the app:

```bash
autowire run
```

Autowire starts the server at:

```text
http://127.0.0.1:8000
```

Detected routes:

```text
GET  /users
POST /users
WS   /stats
```

Try it:

```bash
curl http://127.0.0.1:8000/users
```

```bash
curl -X POST http://127.0.0.1:8000/users \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Alice\"}"
```

## CLI

Run a project from its root folder:

```bash
autowire run
```

Use a custom routes folder:

```bash
autowire run --routes app_routes
```

Change host and port:

```bash
autowire run --host 0.0.0.0 --port 8000
```

Enable rate limiting from the CLI:

```bash
autowire run --rate-limit 120 --rate-period 60
```

## Python Entrypoint

For production deployments, create an explicit ASGI entrypoint such as `app.py`:

```python
from autowire import create_app

app = create_app("routes")
```

Run with Uvicorn:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

## Request Object

HTTP handlers receive a `request` object.

```python
@get
def fetch(request):
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
request.method   # "GET", "POST", "PUT", ...
request.path     # "/users"
request.headers  # lowercase dict
request.query    # dict[str, list[str]]
request.body     # parsed JSON dict, raw bytes, or {}
request.user     # authenticated user payload, or None
```

## Responses

Return JSON:

```python
return {"ok": True}
```

Return JSON with status:

```python
return {"created": True}, 201
```

Return plain text:

```python
return "hello"
```

Return empty response:

```python
return None
```

## WebSockets

WebSocket handlers receive a `socket` object.

Echo server:

```python
from autowire import websocket


@websocket
async def connect(socket):
    await socket.send("Welcome")
    async for message in socket:
        await socket.send(f"Echo: {message}")
```

Stats stream:

```python
import asyncio

from autowire import websocket


@websocket
async def connect(socket):
    while True:
        await socket.send({"active": True})
        await asyncio.sleep(15)
```

## Database

Autowire includes a small SQLite helper for projects that need persistence
without setting up a full database server.

```python
from autowire import get_database

db = get_database()
```

Default path:

```text
data/autowire.db
```

Set a custom path:

Windows PowerShell:

```powershell
$env:AUTOWIRE_DB_PATH = "data/my-app.db"
```

Linux/macOS:

```bash
export AUTOWIRE_DB_PATH=data/my-app.db
```

The folder and database file are created automatically.

Basic usage:

```python
await db.executescript(
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL
    );
    """
)

user_id = await db.execute(
    "INSERT INTO users (name) VALUES (?)",
    ("Alice",),
)

users = await db.fetch_all("SELECT id, name FROM users")
```

## Rate Limiting

Rate limiting can be enabled in an ASGI entrypoint:

```python
from autowire import RateLimit, ServerRateLimitConfig, create_app

app = create_app(
    "routes",
    rate_limit=ServerRateLimitConfig(
        default_limit=RateLimit(rate=120, period=60),
    ),
)
```

This means each identity gets up to `120` requests per `60` seconds.

## Authentication

Autowire auth is configured once when creating the app. Route files do not need
to repeat auth checks.

Autowire supports:

- API token auth
- JWT bearer auth
- login/password auth that issues JWTs
- database-backed resolver callbacks for real users

If multiple auth modes are enabled, any valid mode can authenticate the request.

### API Token Auth For Real Users

Use `api_token_resolver` when every requester has their own API token.

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
curl http://localhost:8000/users -H "X-API-Token: user-token"
```

### Static API Tokens

Static tokens are useful for simple internal tools.

```python
from autowire import AuthConfig, create_app, parse_api_tokens

app = create_app(
    "routes",
    auth=AuthConfig(
        api_token_enabled=True,
        api_tokens=parse_api_tokens("token-1,token-2"),
    ),
)
```

### Login And Password For Real Users

Use `credential_resolver` when every requester has their own login.

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

    # Replace this with bcrypt/argon2 password hash verification in production.
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

Login request:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"alice\",\"password\":\"alice-password\"}"
```

Response:

```json
{
  "access_token": "<jwt>",
  "token_type": "bearer"
}
```

Use the token:

```bash
curl http://localhost:8000/users \
  -H "Authorization: Bearer <jwt>"
```

### JWT Auth

If your app already issues JWTs somewhere else, Autowire can verify them:

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

WebSockets can authenticate with a bearer header:

```text
Authorization: Bearer <jwt>
```

or with a query token:

```text
ws://localhost:8000/stats?token=<jwt>
```

## Configuration Pattern

Autowire does not force a configuration system. A common pattern is to read
environment variables in your own `app.py`.

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

## Deployment

Create `app.py`:

```python
from autowire import create_app

app = create_app("routes")
```

Run:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

For production, run behind a reverse proxy such as Nginx/Caddy and use a process
manager such as systemd, Docker, or your hosting provider's service manager.

## Relationship To Supporting Packages

Autowire is designed to work with your supporting packages later:

- `ws-reconnect-manager` for reconnecting client-side WebSocket connections.
- `smart-api-limiter` or a server rate-limiter package for external rate-limit
  implementations.

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

Install locally:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

## License

MIT
