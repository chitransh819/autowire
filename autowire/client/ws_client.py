"""WebSocket client hook for the existing reconnect manager."""

from __future__ import annotations

from typing import Any


class AutoWebSocketClient:
    """Thin adapter for the client-side ws-reconnect-manager package."""

    def __init__(self, url: str, **options: Any) -> None:
        try:
            from ws_reconnect_manager import ReconnectingWebSocketClient
        except ImportError as exc:
            raise RuntimeError(
                "Install the client-side ws-reconnect-manager package before enabling "
                "generated WebSocket clients."
            ) from exc
        self.client = ReconnectingWebSocketClient(url, **options)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)
