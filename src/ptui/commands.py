"""The command registry — the single source of truth for keymaps, help and hints.

Handlers are plain functions taking the app first; register them with
``@command``. Adding a command means adding one decorated function, nothing else.
"""

from __future__ import annotations

import inspect
import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Command:
    """One entry of the command registry.

    Attributes:
        name: Dotted command name, such as `nav.down`; what keys.toml's `cmd` holds.
        description: One-line description, shown in help and in `KEYS.md`.
        function: The handler: takes the app first, then the command's own arguments.
    """

    name: str
    description: str
    function: Callable[..., Any]


REGISTRY: dict[str, Command] = {}


def command(name: str, description: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Build the decorator that registers a command handler.

    Args:
        name: Dotted command name, such as `nav.down`.
        description: One-line description for help and `KEYS.md`.

    Returns:
        A decorator that adds the handler to `REGISTRY` and hands it back unchanged.
    """

    def register(function: Callable[..., Any]) -> Callable[..., Any]:
        """Add a handler to `REGISTRY`.

        Args:
            function: The handler, taking the app first.

        Returns:
            The same handler, so it stays callable by its own name.

        Raises:
            ValueError: When the command name is already registered.
        """
        if name in REGISTRY:
            raise ValueError(f"duplicate command {name!r}")
        REGISTRY[name] = Command(name, description, function)
        return function

    return register


def run(name: str, app: Any, args: dict[str, Any] | None = None) -> Any:
    """Run a command by name.

    Args:
        name: Dotted command name.
        app: The running `PtuiApp`.
        args: Keyword arguments for the handler, or `None` for none.

    Returns:
        Whatever the handler returns.

    Raises:
        KeyError: When no command has that name.
    """
    registered = REGISTRY.get(name)
    if registered is None:
        raise KeyError(name)
    return registered.function(app, **(args or {}))


def params(name: str) -> list[inspect.Parameter]:
    """A command's own parameters — everything after the leading `app`.

    Args:
        name: Dotted name of a registered command.

    Returns:
        The handler's `inspect.Parameter` objects in signature order, without `app`.
    """
    return list(inspect.signature(REGISTRY[name].function).parameters.values())[1:]


def _argument(param: inspect.Parameter) -> str:
    """Render one parameter the way the `:` prompt shows it.

    For example `<key: str>` or `[unmark: bool = false]`. A `None` or empty default is left unsaid, as
    is `| None`: the brackets already say it may be left out.

    Args:
        param: One of the handler's parameters, as `params` returns it.

    Returns:
        The parameter in angle brackets when required, in square brackets when optional.
    """
    text = f"{param.name}: {str(param.annotation).removesuffix(' | None')}"
    if param.default is inspect.Parameter.empty:
        return f"<{text}>"
    if param.default not in (None, ""):
        text += f" = {json.dumps(param.default)}"  # TOML's spelling: `false`, not `False`
    return f"[{text}]"


def signature(name: str) -> str:
    """Render the argument line the `:` prompt shows, like `<key: str> [reverse: bool]`.

    Each argument appears with its type and default. Optional parameters are bracketed, so what may be
    left out is visible while typing.

    Args:
        name: Dotted name of a registered command.

    Returns:
        The rendered arguments joined by spaces, empty for a command that takes none.
    """
    return " ".join(_argument(param) for param in params(name))


TRUTHY = frozenset({"true", "yes", "on", "1"})


def _coerce(value: str, annotation: str) -> Any:
    """Turn text into what the parameter wants.

    The annotation is a *string* here (`from __future__ import annotations` defers them), so the test
    is textual, which is also what makes `bool | None` and `int | None` work without unwrapping
    anything. `bool` first: `"false"` is a perfectly true string.

    Args:
        value: The argument as typed.
        annotation: The parameter's annotation, as text.

    Returns:
        A `bool`, `float` or `int` when the annotation names one, else `value` unchanged.

    Raises:
        ValueError: When the text is not a valid `float` or `int`.
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

    Args:
        name: Dotted name of a registered command.
        text: The argument line as typed, quoted the way `shlex` reads it.

    Returns:
        Parameter name to coerced value, for the arguments given only.

    Raises:
        ValueError: When there are more arguments than parameters, or a number does not parse.
    """
    values = shlex.split(text)
    names = params(name)
    if len(values) > len(names):
        raise ValueError(f"{name} takes at most {len(names)} argument(s): {signature(name)}")
    return {param.name: _coerce(value, str(param.annotation)) for param, value in zip(names, values, strict=False)}
