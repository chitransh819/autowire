"""Dynamic route module loading."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True, slots=True)
class RouteModule:
    path: str
    name: str
    file: Path
    module: ModuleType


def scan_routes(folder: str | Path = "routes") -> dict[str, RouteModule]:
    """Load every Python file in a route folder.

    Files starting with an underscore are ignored. Route modules do not need the
    folder to be a Python package.
    """

    route_dir = Path(folder).resolve()
    if not route_dir.exists():
        raise FileNotFoundError(f"routes folder does not exist: {route_dir}")
    if not route_dir.is_dir():
        raise NotADirectoryError(f"routes path is not a folder: {route_dir}")

    routes: dict[str, RouteModule] = {}
    for file in sorted(route_dir.glob("*.py")):
        if file.name.startswith("_"):
            continue
        name = file.stem
        path = f"/{name.replace('_', '-')}"
        module = _load_module(file, module_name=f"autowire_user_routes.{name}")
        routes[path] = RouteModule(path=path, name=name, file=file, module=module)
    return routes


def _load_module(file: Path, *, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import route module from {file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

