from __future__ import annotations

import shutil
from typing import Any
from pathlib import Path
from uuid import uuid4

import pytest

from autowire import ConnectionHub, NotificationStore, SQLiteDatabase


class FakeSocket:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def send(self, payload: Any) -> None:
        self.messages.append(payload)


@pytest.fixture
def notification_store() -> NotificationStore:
    root = Path("test-workspace") / uuid4().hex
    root.mkdir(parents=True)
    try:
        yield NotificationStore(SQLiteDatabase(root / "notifications.db"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_send_to_connected_user_delivers_to_all_connections() -> None:
    hub = ConnectionHub()
    first = FakeSocket()
    second = FakeSocket()

    await hub.connect("user-1", first)
    await hub.connect("user-1", second)

    result = await hub.send_to_user("user-1", {"type": "notification"})

    assert result.delivered is True
    assert result.connections == 2
    assert first.messages == [{"type": "notification"}]
    assert second.messages == [{"type": "notification"}]


@pytest.mark.asyncio
async def test_send_to_disconnected_user_returns_not_connected() -> None:
    hub = ConnectionHub()

    result = await hub.send_to_user("user-1", {"type": "notification"})

    assert result.delivered is False
    assert result.reason == "not_connected"
    assert result.as_dict() == {
        "delivered": False,
        "user_id": "user-1",
        "connections": 0,
        "reason": "not_connected",
    }


@pytest.mark.asyncio
async def test_connection_context_unregisters_socket() -> None:
    hub = ConnectionHub()
    socket = FakeSocket()

    async with hub.connection("user-1", socket):
        assert await hub.is_connected("user-1")

    assert not await hub.is_connected("user-1")


@pytest.mark.asyncio
async def test_notification_store_sends_to_connected_user_without_storing(
    notification_store: NotificationStore,
) -> None:
    hub = ConnectionHub()
    socket = FakeSocket()
    await hub.connect("user-1", socket)

    result = await notification_store.send_to_user(
        "user-1",
        {"type": "notification"},
        hub=hub,
    )

    assert result.delivery.delivered is True
    assert result.stored is False
    assert socket.messages == [{"type": "notification"}]
    assert await notification_store.pending_for("user-1") == []


@pytest.mark.asyncio
async def test_notification_store_saves_pending_when_user_is_not_connected(
    notification_store: NotificationStore,
) -> None:
    hub = ConnectionHub()

    result = await notification_store.send_to_user(
        "user-1",
        {"type": "notification", "message": "Later"},
        hub=hub,
    )
    pending = await notification_store.pending_for("user-1")

    assert result.delivery.delivered is False
    assert result.delivery.reason == "not_connected"
    assert result.stored is True
    assert result.notification_id == pending[0].id
    assert pending[0].payload == {"type": "notification", "message": "Later"}


@pytest.mark.asyncio
async def test_notification_store_flushes_pending_messages_on_connection(
    notification_store: NotificationStore,
) -> None:
    socket = FakeSocket()
    await notification_store.store("user-1", "first")
    await notification_store.store("user-1", {"type": "second"})

    async with notification_store.connection("user-1", socket) as flush:
        assert flush.delivered == 2
        assert socket.messages == ["first", {"type": "second"}]

    assert await notification_store.pending_for("user-1") == []
