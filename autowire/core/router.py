"""Route wiring from loaded modules into an Autowire app."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .loader import RouteModule


@dataclass(frozen=True, slots=True)
class RouteDefinition:
    path: str
    method: str
    name: str
    endpoint: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class WebSocketDefinition:
    path: str
    name: str
    endpoint: Callable[..., Any]


def wire(app: Any, routes: Mapping[str, RouteModule]) -> None:
    for path, route_module in routes.items():
        for definition in iter_route_definitions(path, route_module):
            app.add_route(definition.path, definition.endpoint, definition.method, name=definition.name)
        for definition in iter_websocket_definitions(path, route_module):
            app.add_websocket(definition.path, definition.endpoint, name=definition.name)


def iter_route_definitions(path: str, route_module: RouteModule) -> list[RouteDefinition]:
    definitions: list[RouteDefinition] = []
    for attr in dir(route_module.module):
        endpoint = getattr(route_module.module, attr)
        method = getattr(endpoint, "_autowire_method", None)
        if method is None:
            continue
        definitions.append(
            RouteDefinition(path=path, method=method, name=attr, endpoint=endpoint)
        )
    return definitions


def iter_websocket_definitions(path: str, route_module: RouteModule) -> list[WebSocketDefinition]:
    definitions: list[WebSocketDefinition] = []
    for attr in dir(route_module.module):
        endpoint = getattr(route_module.module, attr)
        if not getattr(endpoint, "_autowire_websocket", False):
            continue
        definitions.append(WebSocketDefinition(path=path, name=attr, endpoint=endpoint))
    return definitions

