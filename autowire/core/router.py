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
    auth_required: bool | None


@dataclass(frozen=True, slots=True)
class WebSocketDefinition:
    path: str
    name: str
    endpoint: Callable[..., Any]
    auth_required: bool | None


def wire(app: Any, routes: Mapping[str, RouteModule]) -> None:
    for path, route_module in routes.items():
        for definition in iter_route_definitions(path, route_module):
            app.add_route(
                definition.path,
                definition.endpoint,
                definition.method,
                name=definition.name,
                auth_required=definition.auth_required,
            )
        for definition in iter_websocket_definitions(path, route_module):
            app.add_websocket(
                definition.path,
                definition.endpoint,
                name=definition.name,
                auth_required=definition.auth_required,
            )


def iter_route_definitions(path: str, route_module: RouteModule) -> list[RouteDefinition]:
    definitions: list[RouteDefinition] = []
    for attr in dir(route_module.module):
        endpoint = getattr(route_module.module, attr)
        method = getattr(endpoint, "_autowire_method", None)
        if method is None:
            continue
        definitions.append(
            RouteDefinition(
                path=_endpoint_path(attr, endpoint),
                method=method,
                name=attr,
                endpoint=endpoint,
                auth_required=getattr(endpoint, "_autowire_auth_required", None),
            )
        )
    return definitions


def iter_websocket_definitions(path: str, route_module: RouteModule) -> list[WebSocketDefinition]:
    definitions: list[WebSocketDefinition] = []
    for attr in dir(route_module.module):
        endpoint = getattr(route_module.module, attr)
        if not getattr(endpoint, "_autowire_websocket", False):
            continue
        definitions.append(
            WebSocketDefinition(
                path=_endpoint_path(attr, endpoint),
                name=attr,
                endpoint=endpoint,
                auth_required=getattr(endpoint, "_autowire_auth_required", None),
            )
        )
    return definitions


def _endpoint_path(attr: str, endpoint: Callable[..., Any]) -> str:
    path = getattr(endpoint, "_autowire_path", None)
    if path:
        return str(path)
    return f"/{attr.replace('_', '-')}"
