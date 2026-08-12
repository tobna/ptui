"""The command registry — the single source of truth for keymaps, help and hints.

Handlers are plain functions taking the app first; register them with
``@command``. Adding a command means adding one decorated function, nothing else.
"""

from __future__ import annotations

import inspect
import shlex
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


def params(name: str) -> list[inspect.Parameter]:
    """A command's own parameters — everything after the leading `app`."""
    return list(inspect.signature(REGISTRY[name].fn).parameters.values())[1:]


def signature(name: str) -> str:
    """`key [reverse]` — the argument line the `:` prompt shows. Optional
    parameters are bracketed, so what may be left out is visible while typing."""
    return " ".join(
        p.name if p.default is inspect.Parameter.empty else f"[{p.name}]" for p in params(name)
    )


TRUTHY = frozenset({"true", "yes", "on", "1"})


def _coerce(value: str, annotation: str) -> Any:
    """Text to what the parameter wants. The annotation is a *string* here —
    `from __future__ import annotations` defers them — so the test is textual,
    which is also what makes `bool | None` and `int | None` work without
    unwrapping anything. `bool` first: `"false"` is a perfectly true string.
    """
    if "bool" in annotation:
        return value.casefold() in TRUTHY
    if "float" in annotation:
        return float(value)
    if "int" in annotation:
        return int(value)
    return value


def parse_args(name: str, text: str) -> dict[str, Any]:
    """`sort.by`, `"year true"` -> `{"key": "year", "reverse": True}`.

    Positional and `shlex`-quoted, so an argument with spaces is `"like this"`.
    Anything left out keeps the command's own default — an empty line runs the
    command exactly as a key binding with no `args` would.
    """
    values = shlex.split(text)
    names = params(name)
    if len(values) > len(names):
        raise ValueError(f"{name} takes at most {len(names)} argument(s): {signature(name)}")
    return {
        param.name: _coerce(value, str(param.annotation))
        for param, value in zip(names, values, strict=False)
    }
