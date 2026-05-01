"""Decorators used by route files."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

RouteFunc = TypeVar("RouteFunc", bound=Callable[..., Any])


def _method(
    method: str,
) -> Callable[..., RouteFunc | Callable[[RouteFunc], RouteFunc]]:
    def factory(
        fn: RouteFunc | str | None = None,
        *,
        path: str | None = None,
        auth: bool | None = None,
    ) -> RouteFunc | Callable[[RouteFunc], RouteFunc]:
        if isinstance(fn, str):
            path = fn
            fn = None
        if fn is not None:
            return _tag_route(fn, method=method, path=path, auth=auth)

        def decorate(inner: RouteFunc) -> RouteFunc:
            return _tag_route(inner, method=method, path=path, auth=auth)

        return decorate

    return factory


def _tag_route(
    fn: RouteFunc,
    *,
    method: str,
    path: str | None,
    auth: bool | None,
) -> RouteFunc:
    setattr(fn, "_autowire_method", method)
    setattr(fn, "_autowire_path", path)
    setattr(fn, "_autowire_auth_required", auth)
    return fn


get = _method("GET")
post = _method("POST")
put = _method("PUT")
patch = _method("PATCH")
delete = _method("DELETE")


def websocket(
    fn: RouteFunc | str | None = None,
    *,
    path: str | None = None,
    auth: bool | None = None,
) -> RouteFunc | Callable[[RouteFunc], RouteFunc]:
    if isinstance(fn, str):
        path = fn
        fn = None
    if fn is not None:
        return _tag_websocket(fn, path=path, auth=auth)

    def decorate(inner: RouteFunc) -> RouteFunc:
        return _tag_websocket(inner, path=path, auth=auth)

    return decorate


def _tag_websocket(fn: RouteFunc, *, path: str | None, auth: bool | None) -> RouteFunc:
    setattr(fn, "_autowire_websocket", True)
    setattr(fn, "_autowire_path", path)
    setattr(fn, "_autowire_auth_required", auth)
    return fn
