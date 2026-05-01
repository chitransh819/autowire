"""Command line interface for Autowire."""

from __future__ import annotations

import argparse
from pathlib import Path

from .core.rate_limiter import RateLimit, ServerRateLimitConfig
from .core.server import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="autowire")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="run an Autowire server")
    run_parser.add_argument("--routes", default="routes", help="routes folder to scan")
    run_parser.add_argument("--host", default="127.0.0.1", help="host to bind")
    run_parser.add_argument("--port", type=int, default=8000, help="port to bind")
    run_parser.add_argument("--reload", action="store_true", help="enable Uvicorn reload")
    run_parser.add_argument("--rate-limit", type=int, default=None, help="requests per period")
    run_parser.add_argument("--rate-period", type=float, default=60.0, help="rate limit period seconds")
    run_parser.add_argument("--rate-burst", type=int, default=None, help="optional burst size")

    args = parser.parse_args(argv)
    if args.command in {None, "run"}:
        _run(args)


def _run(args: argparse.Namespace) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install uvicorn or install Autowire from pyproject.toml first.") from exc

    rate_limit = None
    if args.rate_limit is not None:
        rate_limit = ServerRateLimitConfig(
            default_limit=RateLimit(
                rate=args.rate_limit,
                period=args.rate_period,
                burst=args.rate_burst,
            )
        )

    app = create_app(Path(args.routes), rate_limit=rate_limit)
    described = app.app.describe_routes() if hasattr(app, "app") else app.describe_routes()
    print(f"Server live at http://{args.host}:{args.port}")
    print("Routes auto-detected:")
    for route in described:
        print(f"  {route}")
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
