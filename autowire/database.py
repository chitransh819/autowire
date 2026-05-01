"""Small SQLite helper for Autowire apps."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

Params = Sequence[Any] | Mapping[str, Any]

DEFAULT_DB_PATH = Path("data") / "autowire.db"


class SQLiteDatabase:
    """Async-friendly SQLite wrapper backed by the standard library."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("AUTOWIRE_DB_PATH") or DEFAULT_DB_PATH
        self.path = Path(configured).expanduser()
        self._lock = asyncio.Lock()

    async def execute(self, sql: str, params: Params = ()) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._execute_sync, sql, params)

    async def fetch_one(self, sql: str, params: Params = ()) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._fetch_one_sync, sql, params)

    async def fetch_all(self, sql: str, params: Params = ()) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._fetch_all_sync, sql, params)

    async def executescript(self, sql: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._executescript_sync, sql)

    async def execute_many(self, statements: Iterable[tuple[str, Params]]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._execute_many_sync, list(statements))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _execute_sync(self, sql: str, params: Params) -> int:
        with self._connect() as connection:
            cursor = connection.execute(sql, params)
            connection.commit()
            return int(cursor.lastrowid)

    def _fetch_one_sync(self, sql: str, params: Params) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def _fetch_all_sync(self, sql: str, params: Params) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def _executescript_sync(self, sql: str) -> None:
        with self._connect() as connection:
            connection.executescript(sql)
            connection.commit()

    def _execute_many_sync(self, statements: list[tuple[str, Params]]) -> None:
        with self._connect() as connection:
            for sql, params in statements:
                connection.execute(sql, params)
            connection.commit()


_default_database: SQLiteDatabase | None = None


def get_database(path: str | Path | None = None) -> SQLiteDatabase:
    """Return an app database, using AUTOWIRE_DB_PATH or data/autowire.db by default."""

    global _default_database
    if path is not None:
        return SQLiteDatabase(path)
    if _default_database is None:
        _default_database = SQLiteDatabase()
    return _default_database

