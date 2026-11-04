"""Keys.toml loading, chord resolution, and the prefix invariant check.

Hard invariant (SPEC): within a mode, no binding may be a proper prefix of
another. `conflicts()` runs at config load and the app refuses to start if it
returns anything — a shadowed key is invisible until someone wonders why `o`
feels slow.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ptui import commands
from ptui.config import DEFAULTS_DIR, config_dir, read_toml

type Chord = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Binding:
    """One key sequence bound to a command.

    Attributes:
        chord: The key tokens pressed in sequence.
        command: Dotted name of the bound command.
        args: Keyword arguments for the handler; empty for none.
        description: keys.toml's short label, or the command's own description.
    """

    chord: Chord
    command: str
    args: dict[str, Any]
    description: str

    @property
    def keys(self) -> str:
        """The chord as keys.toml writes it.

        Returns:
            The key tokens joined by spaces, such as `g o`.
        """
        return " ".join(self.chord)


@dataclass(frozen=True, slots=True)
class Keymap:
    """Every binding, grouped by mode.

    Attributes:
        modes: Mode name to its bindings, keyed by chord.
        options: The `[options]` table of keys.toml.
        unknown_commands: Sentences naming bindings whose command does not exist.
    """

    modes: dict[str, dict[Chord, Binding]]
    options: dict[str, Any]
    unknown_commands: tuple[str, ...]

    def option(self, key: str, default: Any = None) -> Any:
        """Look up a keys.toml option.

        Args:
            key: Option name.
            default: What to return when the option is unset.

        Returns:
            The option's value, or `default`.
        """
        return self.options.get(key, default)

    def lookup(self, mode: str, chord: Chord) -> Binding | None:
        """Find the binding of a complete chord.

        Args:
            mode: Mode name.
            chord: The key tokens pressed so far.

        Returns:
            The binding, or `None` when the chord binds nothing in that mode.
        """
        return self.modes.get(mode, {}).get(chord)

    def is_prefix(self, mode: str, chord: Chord) -> bool:
        """Tell whether a chord is the start of a longer one.

        Args:
            mode: Mode name.
            chord: The key tokens pressed so far.

        Returns:
            `True` when some binding of the mode continues the chord.
        """
        length = len(chord)
        return any(candidate[:length] == chord and len(candidate) > length for candidate in self.modes.get(mode, {}))

    def under_prefix(self, mode: str, chord: Chord) -> list[Binding]:
        """Bindings that continue `chord` — what which-key shows.

        Args:
            mode: Mode name.
            chord: The key tokens pressed so far.

        Returns:
            The continuing bindings, sorted by their keys.
        """
        length = len(chord)
        return sorted(
            (
                binding
                for candidate, binding in self.modes.get(mode, {}).items()
                if candidate[:length] == chord and len(candidate) > length
            ),
            key=lambda binding: binding.keys,
        )

    def for_command(self, mode: str, command: str) -> str | None:
        """The keys bound to a command, for help and the hint bar.

        Args:
            mode: Mode name.
            command: Dotted command name.

        Returns:
            The keys of the first binding of that command, or `None` when it has none in the mode.
        """
        return next((binding.keys for binding in self.modes.get(mode, {}).values() if binding.command == command), None)

    def conflicts(self) -> list[str]:
        """Human-readable descriptions of every prefix collision, empty if clean.

        Returns:
            One sentence per colliding pair of chords, sorted.
        """
        descriptions = []
        for mode, bindings in self.modes.items():
            for chord in bindings:
                for other in bindings:
                    if other != chord and other[: len(chord)] == chord:
                        descriptions.append(
                            f"[modes.{mode}] {' '.join(chord)!r} shadows {' '.join(other)!r} "
                            f"({bindings[chord].command} vs {bindings[other].command})"
                        )
        return sorted(descriptions)


def normalize(key: str, character: str | None = None) -> str:
    r"""A Textual key event as the token keys.toml uses.

    Printable characters bind as themselves (`?`, `\`, `1`); everything else
    uses Textual's key name (`ctrl+d`, `escape`, `shift+tab`).

    Args:
        key: The event's key name.
        character: The event's printable character, or `None` when it has none.

    Returns:
        The token keys.toml writes for that key.
    """
    if character == " ":
        return "space"
    if character and character.isprintable():
        return character
    return key


PANES = ("list", "info", "files", "log")
"""The modes `[global]` reaches: every pane, but not the picker, whose filter box
has the keyboard — a letter bound there would never reach the filter."""


def _table(section: str, bindings: dict[str, Any], path: Path, unknown: list[str]) -> dict[Chord, Binding | None]:
    """Read one keys.toml table as chords.

    A key set to `false` becomes `None`. Unknown commands are collected, not fatal.

    Args:
        section: The table's name, for messages.
        bindings: Key sequence to its binding, or to `false`.
        path: The file the table came from, for messages.
        unknown: Receives one sentence per unknown command.

    Returns:
        Chord to its `Binding`, or to `None` for an unbound key.

    Raises:
        ValueError: When a binding has no `cmd`.
    """
    table: dict[Chord, Binding | None] = {}
    for keys, spec in bindings.items():
        chord = tuple(normalize(key) for key in keys.split(" ") if key)
        if spec is False:
            table[chord] = None
            continue
        if not isinstance(spec, dict) or "cmd" not in spec:
            raise ValueError(
                f'{path}: [{section}] {keys!r} needs a cmd, like {{ cmd = "nav.down" }}, or false to unbind it'
            )
        command_name = spec["cmd"]
        registered = commands.REGISTRY.get(command_name)
        if registered is None:
            unknown.append(f"[{section}] {keys!r} -> unknown command {command_name!r}")
        table[chord] = Binding(
            chord=chord,
            command=command_name,
            args=spec.get("args", {}),
            description=spec.get("desc") or (registered.description if registered else ""),
        )
    return table


def _collide(first: Chord, second: Chord) -> bool:
    """The same key, or one a prefix of the other: one mode cannot bind both.

    Args:
        first: One chord.
        second: The other chord.

    Returns:
        `True` when the two chords cannot both be bound in a mode.
    """
    length = min(len(first), len(second))
    return first[:length] == second[:length]


def _over(top: dict[Chord, Binding | None], under: dict[Chord, Binding | None]) -> dict[Chord, Binding | None]:
    """Lay one table of bindings over another.

    An entry of `under` survives only if it collides with nothing in `top`.

    Args:
        top: The table that wins.
        under: The table that gives way.

    Returns:
        The surviving entries of `under` followed by all of `top`.
    """
    kept = {
        chord: binding for chord, binding in under.items() if not any(_collide(chord, top_chord) for top_chord in top)
    }
    return {**kept, **top}


def _modes(data: dict[str, Any], path: Path, unknown: list[str]) -> dict[str, dict[Chord, Binding | None]]:
    """Read one keys.toml as per-mode tables.

    `[global]` is laid under each pane's own table, which wins on the same key and on a shared prefix:
    a pane's `g` drops the global `g o` in that pane only, rather than a conflict refusing to start.

    Args:
        data: The parsed keys.toml.
        path: The file it came from, for messages.
        unknown: Receives one sentence per unknown command.

    Returns:
        Mode name to its table of chords, with `None` for an unbound key.
    """
    shared = _table("global", data.get("global", {}), path, unknown)
    tables = {
        mode: _table(f"modes.{mode}", bindings, path, unknown) for mode, bindings in data.get("modes", {}).items()
    }
    for mode in PANES:
        tables[mode] = _over(tables.get(mode, {}), shared)
    return tables


def load(path: Path | None = None) -> Keymap:
    """The shipped keymap with the user's keys.toml laid over it, key by key.

    A user binding always adds: it drops only the shipped bindings it would
    collide with in the modes it reaches — the same key, or a chord one is a
    prefix of — so swapping two keys takes two lines, and a command bound to a
    new key keeps its shipped ones. `"o" = false` drops `o` (and everything under
    it, for a prefix) without binding anything; it is the one way to remove a
    shipped key. The conflict check is left with the collisions inside the
    user's own tables.

    `[global]` is laid under every pane's own table: one copy of the pane keys
    rather than one per mode, which is how a mode once ended up with none and
    trapped the keyboard.

    Args:
        path: The user's keys file; `None` means `<config_dir>/keys.toml`.

    Returns:
        The merged `Keymap`.

    Raises:
        ValueError: When either file holds a binding with no `cmd`, or the user's is not valid TOML.
    """
    shipped_path = DEFAULTS_DIR / "keys.toml"
    shipped = tomllib.loads(shipped_path.read_text())
    if path is None:
        path = config_dir() / "keys.toml"
    user = read_toml(path) if path.is_file() else {}

    unknown: list[str] = []
    ours, theirs = _modes(shipped, shipped_path, unknown), _modes(user, path, unknown)
    modes = {}
    for mode in {**ours, **theirs}:
        merged = _over(theirs.get(mode, {}), ours.get(mode, {}))
        modes[mode] = {chord: binding for chord, binding in merged.items() if binding is not None}
    options = {**shipped.get("options", {}), **user.get("options", {})}
    return Keymap(modes, options, tuple(sorted(unknown)))
