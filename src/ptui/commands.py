"""The command registry — the single source of truth for keymaps, help and hints.

Handlers are plain functions taking the app first; register them with
``@command``. Adding a command means adding one decorated function, nothing else.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    desc: str
    fn: Callable[..., Any]


REGISTRY: dict[str, Command] = {}


def command(name: str, desc: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in REGISTRY:
            raise ValueError(f"duplicate command {name!r}")
        REGISTRY[name] = Command(name, desc, fn)
        return fn

    return register


def run(name: str, app: Any, args: dict[str, Any] | None = None) -> Any:
    cmd = REGISTRY.get(name)
    if cmd is None:
        raise KeyError(name)
    return cmd.fn(app, **(args or {}))
