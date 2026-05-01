"""Decorators used by route files."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

RouteFunc = TypeVar("RouteFunc", bound=Callable[..., Any])


def _method(method: str) -> Callable[[RouteFunc], RouteFunc]:
    def decorate(fn: RouteFunc) -> RouteFunc:
        setattr(fn, "_autowire_method", method)
        return fn

    return decorate


get = _method("GET")
post = _method("POST")
put = _method("PUT")
patch = _method("PATCH")
delete = _method("DELETE")


def websocket(fn: RouteFunc) -> RouteFunc:
    setattr(fn, "_autowire_websocket", True)
    return fn

