from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from autowire import SQLiteDatabase


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


@pytest.mark.asyncio
async def test_sqlite_database_creates_parent_folder_and_persists_rows(
    workspace_tmp: Path,
) -> None:
    db = SQLiteDatabase(workspace_tmp / "nested" / "app.db")

    await db.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        """
    )
    user_id = await db.execute("INSERT INTO users (name) VALUES (?)", ("Chitransh",))
    rows = await db.fetch_all("SELECT id, name FROM users")

    assert user_id == 1
    assert rows == [{"id": 1, "name": "Chitransh"}]
