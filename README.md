# Autowire

Autowire is a plug-and-play Python backend framework for APIs and WebSockets.

You create files inside a `routes/` folder. Autowire scans them, detects the
decorators, and wires the server automatically.

```text
routes/
  users.py   -> GET /users and POST /users
  chat.py    -> WebSocket /chat
  stats.py   -> WebSocket /stats
```

No router registration. No app boilerplate. Drop a file, write the logic, run
the server.

## Why Autowire?

Most small backend projects repeat the same setup:

- create a server app
- create a router
- import every route module
- register every endpoint
- wire WebSockets separately
- add rate limiting
- add auth
- add a simple database

Autowire turns that into a file-based workflow. The framework handles the wiring
so developers can focus on business logic.

## Features

- File-based HTTP routes.
- File-based WebSocket routes.
- `@get`, `@post`, `@put`, `@patch`, `@delete`, and `@websocket` decorators.
- ASGI app that runs with Uvicorn.
- Optional endpoint-aware rate limiting.
- Optional API token authentication.
- Optional JWT authentication.
- Optional login/password endpoint that issues JWTs.
- SQLite helper with automatic default database creation.
- Small client adapter hook for `ws-reconnect-manager`.

## Installation

For local development from source:

```bash
pip install -e ".[dev]"
```

When published later:

```bash
pip install autowire
```

## Quick Start

Create a project:

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


@get
async def fetch(request):
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        """
    )
    return {
        "users": await db.fetch_all("SELECT id, name FROM users ORDER BY id")
    }


@post
async def create(request):
    name = request.body["name"]
    user_id = await db.execute("INSERT INTO users (name) VALUES (?)", (name,))
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
        stats = await db.fetch_one("SELECT COUNT(*) AS total_users FROM users")
        await socket.send(stats or {"total_users": 0})
        await asyncio.sleep(15)
```

Run:

```bash
autowire run
```

The server starts at:

```text
http://127.0.0.1:8000
```

Generated routes:

```text
GET  /users
POST /users
WS   /stats
```

## Route Rules

Each Python file in `routes/` becomes a URL path.

```text
routes/users.py       -> /users
routes/user_stats.py  -> /user-stats
routes/chat.py        -> /chat
```

Functions become handlers when decorated:

```python
from autowire import get, post, websocket


@get
def fetch(request):
    return {"ok": True}


@post
def create(request):
    return {"created": request.body}


@websocket
async def connect(socket):
    await socket.send("connected")
```

## Request Object

HTTP route functions receive a `request` object.

Useful properties:

```python
request.method   # "GET", "POST", ...
request.path     # "/users"
request.headers  # lowercase dict
request.query    # dict[str, list[str]]
request.body     # parsed JSON dict, raw bytes, or {}
request.user     # auth payload when auth is enabled
```

Return values:

```python
return {"ok": True}          # JSON 200
return {"created": True}, 201
return "plain text"
return None                  # 204
```

## WebSockets

WebSocket route functions receive a `socket` object.

```python
from autowire import websocket


@websocket
async def connect(socket):
    await socket.send("Welcome")
    async for message in socket:
        await socket.send(f"Echo: {message}")
```

## Database

Autowire includes a tiny SQLite helper for plug-and-play persistence.

```python
from autowire import get_database

db = get_database()
```

Default database path:

```text
data/autowire.db
```

Override it:

```bash
set AUTOWIRE_DB_PATH=data/my-app.db
```

or on Linux/macOS:

```bash
export AUTOWIRE_DB_PATH=data/my-app.db
```

The parent folder and database file are created automatically.

## Rate Limiting

From the CLI:

```bash
autowire run --rate-limit 120 --rate-period 60
```

From Python:

```python
from autowire import RateLimit, ServerRateLimitConfig, create_app

app = create_app(
    "routes",
    rate_limit=ServerRateLimitConfig(
        default_limit=RateLimit(rate=120, period=60),
    ),
)
```

## Authentication

Auth is configured once at app creation. Routes do not need to repeat auth code.

Autowire supports two styles:

- simple static credentials for small internal tools
- resolver callbacks for real apps where every requester has their own API token
  or their own login/password stored in a database

### API Token

For production apps, use `api_token_resolver` so every requester can have their
own token.

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
curl http://localhost:8000/users -H "X-API-Token: token-1"
```

For a tiny internal tool, static tokens also work:

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

### JWT

```python
app = create_app(
    "routes",
    auth=AuthConfig(
        jwt_enabled=True,
        jwt_secret="change-this-secret",
    ),
)
```

Request:

```bash
curl http://localhost:8000/users -H "Authorization: Bearer <jwt>"
```

### Login ID And Password

For production apps, use `credential_resolver` so each requester can log in with
their own credentials.

```python
from autowire import AuthConfig, create_app, get_database

db = get_database()


async def resolve_credentials(username: str, password: str):
    user = await db.fetch_one(
        """
        SELECT id, username, password
        FROM users
        WHERE username = ?
        """,
        (username,),
    )
    if user is None:
        return None

    # In a real app, store password hashes and verify with bcrypt/argon2.
    if user["password"] != password:
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
curl -X POST http://localhost:8000/auth/login ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"admin\",\"password\":\"change-this-password\"}"
```

Use the returned token:

```bash
curl http://localhost:8000/users -H "Authorization: Bearer <token>"
```

### Multiple Auth Modes

You can enable API token, JWT, and login together. If any enabled auth method is
valid, the request is accepted.

WebSockets can authenticate with:

```text
Authorization: Bearer <token>
```

or:

```text
ws://localhost:8000/stats?token=<token>
```

## Python Entrypoint

For production servers, create your own `app.py`:

```python
from autowire import AuthConfig, RateLimit, ServerRateLimitConfig, create_app

app = create_app(
    "routes",
    rate_limit=ServerRateLimitConfig(
        default_limit=RateLimit(rate=120, period=60),
    ),
    auth=AuthConfig(
        api_token_enabled=True,
        api_tokens=frozenset({"server-token"}),
    ),
)
```

Run:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

## Project Structure

This GitHub-ready folder intentionally stays small:

```text
autowire/
  autowire/          # framework source
  examples/routes/   # minimal route examples
  tests/             # framework tests
  pyproject.toml
  README.md
```

Large deployment templates should live in separate repositories or examples
outside the core framework repo.

## Relationship To Your Other Packages

Autowire can later integrate with your separate PyPI packages:

- `ws-reconnect-manager` for generated/reconnecting WebSocket clients.
- `smart-api-limiter` or your server rate-limiter package for externalized rate
  limiting.

For now, Autowire contains small built-in compatible pieces so it works before
those packages are published.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
