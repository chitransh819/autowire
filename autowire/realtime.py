"""WebSocket connection and notification helpers for Autowire apps."""

from __future__ import annotations

import asyncio
import base64
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .database import SQLiteDatabase, get_database


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    user_id: str
    connections: int = 0
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "user_id": self.user_id,
            "connections": self.connections,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class NotificationResult:
    delivery: DeliveryResult
    stored: bool = False
    notification_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "delivery": self.delivery.as_dict(),
            "stored": self.stored,
            "notification_id": self.notification_id,
        }


@dataclass(frozen=True, slots=True)
class PendingNotification:
    id: int
    user_id: str
    payload: Any
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "payload": self.payload,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class FlushResult:
    user_id: str
    attempted: int
    delivered: int
    failed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "attempted": self.attempted,
            "delivered": self.delivered,
            "failed": self.failed,
        }


class ConnectionHub:
    """Tracks connected WebSockets by user id."""

    def __init__(self) -> None:
        self._connections: dict[str, set[Any]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, socket: Any) -> None:
        async with self._lock:
            self._connections[str(user_id)].add(socket)

    async def disconnect(self, user_id: str, socket: Any) -> None:
        async with self._lock:
            sockets = self._connections.get(str(user_id))
            if sockets is None:
                return
            sockets.discard(socket)
            if not sockets:
                self._connections.pop(str(user_id), None)

    @asynccontextmanager
    async def connection(self, user_id: str, socket: Any):
        await self.connect(user_id, socket)
        try:
            yield socket
        finally:
            await self.disconnect(user_id, socket)

    async def send_to_user(self, user_id: str, payload: Any) -> DeliveryResult:
        user_key = str(user_id)
        sockets = await self.connections_for(user_key)
        if not sockets:
            return DeliveryResult(
                delivered=False,
                user_id=user_key,
                reason="not_connected",
            )

        delivered = 0
        stale: list[Any] = []
        for socket in sockets:
            try:
                await socket.send(payload)
                delivered += 1
            except Exception:
                stale.append(socket)

        for socket in stale:
            await self.disconnect(user_key, socket)

        if delivered == 0:
            return DeliveryResult(
                delivered=False,
                user_id=user_key,
                reason="send_failed",
            )
        return DeliveryResult(
            delivered=True,
            user_id=user_key,
            connections=delivered,
        )

    async def broadcast(self, payload: Any) -> dict[str, DeliveryResult]:
        user_ids = await self.connected_user_ids()
        results: dict[str, DeliveryResult] = {}
        for user_id in user_ids:
            results[user_id] = await self.send_to_user(user_id, payload)
        return results

    async def is_connected(self, user_id: str) -> bool:
        return bool(await self.connections_for(str(user_id)))

    async def connections_for(self, user_id: str) -> list[Any]:
        async with self._lock:
            return list(self._connections.get(str(user_id), set()))

    async def connected_user_ids(self) -> list[str]:
        async with self._lock:
            return list(self._connections)

    async def clear(self) -> None:
        async with self._lock:
            self._connections.clear()


class NotificationStore:
    """Stores undelivered notifications and flushes them on reconnect."""

    def __init__(self, database: SQLiteDatabase | None = None) -> None:
        self.database = database or get_database()
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def send_to_user(
        self,
        user_id: str,
        payload: Any,
        *,
        hub: ConnectionHub | None = None,
        store_if_undelivered: bool = True,
    ) -> NotificationResult:
        hub = hub or get_connection_hub()
        delivery = await hub.send_to_user(user_id, payload)
        if delivery.delivered or not store_if_undelivered:
            return NotificationResult(delivery=delivery)

        notification_id = await self.store(user_id, payload)
        return NotificationResult(
            delivery=delivery,
            stored=True,
            notification_id=notification_id,
        )

    async def store(self, user_id: str, payload: Any) -> int:
        await self.ensure_schema()
        return await self.database.execute(
            """
            INSERT INTO autowire_notifications (user_id, payload)
            VALUES (?, ?)
            """,
            (str(user_id), _serialize_payload(payload)),
        )

    async def pending_for(self, user_id: str, *, limit: int = 100) -> list[PendingNotification]:
        await self.ensure_schema()
        rows = await self.database.fetch_all(
            """
            SELECT id, user_id, payload, created_at
            FROM autowire_notifications
            WHERE user_id = ? AND delivered_at IS NULL
            ORDER BY id ASC
            LIMIT ?
            """,
            (str(user_id), int(limit)),
        )
        return [
            PendingNotification(
                id=int(row["id"]),
                user_id=str(row["user_id"]),
                payload=_deserialize_payload(str(row["payload"])),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    async def mark_delivered(self, notification_id: int) -> None:
        await self.ensure_schema()
        await self.database.execute(
            """
            UPDATE autowire_notifications
            SET delivered_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(notification_id),),
        )

    async def flush_user(
        self,
        user_id: str,
        *,
        socket: Any | None = None,
        hub: ConnectionHub | None = None,
        limit: int = 100,
    ) -> FlushResult:
        pending = await self.pending_for(user_id, limit=limit)
        if not pending:
            return FlushResult(user_id=str(user_id), attempted=0, delivered=0)

        delivered = 0
        failed = 0
        for notification in pending:
            was_delivered = await self._deliver_pending(notification, socket=socket, hub=hub)
            if was_delivered:
                delivered += 1
                await self.mark_delivered(notification.id)
            else:
                failed += 1

        return FlushResult(
            user_id=str(user_id),
            attempted=len(pending),
            delivered=delivered,
            failed=failed,
        )

    @asynccontextmanager
    async def connection(
        self,
        user_id: str,
        socket: Any,
        *,
        hub: ConnectionHub | None = None,
        flush_pending: bool = True,
        limit: int = 100,
    ):
        hub = hub or get_connection_hub()
        async with hub.connection(user_id, socket):
            result = FlushResult(user_id=str(user_id), attempted=0, delivered=0)
            if flush_pending:
                result = await self.flush_user(user_id, socket=socket, limit=limit)
            yield result

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await self.database.executescript(
                """
                CREATE TABLE IF NOT EXISTS autowire_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    delivered_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_autowire_notifications_pending
                ON autowire_notifications (user_id, delivered_at, id);
                """
            )
            self._schema_ready = True

    async def _deliver_pending(
        self,
        notification: PendingNotification,
        *,
        socket: Any | None,
        hub: ConnectionHub | None,
    ) -> bool:
        if socket is not None:
            try:
                await socket.send(notification.payload)
            except Exception:
                return False
            return True

        delivery = await (hub or get_connection_hub()).send_to_user(
            notification.user_id,
            notification.payload,
        )
        return delivery.delivered


_default_hub = ConnectionHub()
_default_notification_store: NotificationStore | None = None


def get_connection_hub() -> ConnectionHub:
    return _default_hub


def get_notification_store(database: SQLiteDatabase | None = None) -> NotificationStore:
    global _default_notification_store
    if database is not None:
        return NotificationStore(database)
    if _default_notification_store is None:
        _default_notification_store = NotificationStore()
    return _default_notification_store


def _serialize_payload(payload: Any) -> str:
    if isinstance(payload, bytes):
        return json.dumps(
            {
                "kind": "bytes",
                "data": base64.b64encode(payload).decode("ascii"),
            }
        )
    if isinstance(payload, str):
        return json.dumps({"kind": "text", "data": payload})
    return json.dumps({"kind": "json", "data": payload})


def _deserialize_payload(raw: str) -> Any:
    payload = json.loads(raw)
    kind = payload.get("kind")
    data = payload.get("data")
    if kind == "bytes":
        return base64.b64decode(str(data).encode("ascii"))
    return data
