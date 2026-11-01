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
    chord: Chord
    cmd: str
    args: dict[str, Any]
    desc: str

    @property
    def keys(self) -> str:
        return " ".join(self.chord)


@dataclass(frozen=True, slots=True)
class Keymap:
    modes: dict[str, dict[Chord, Binding]]
    options: dict[str, Any]
    unknown_commands: tuple[str, ...]

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def lookup(self, mode: str, chord: Chord) -> Binding | None:
        return self.modes.get(mode, {}).get(chord)

    def is_prefix(self, mode: str, chord: Chord) -> bool:
        n = len(chord)
        return any(c[:n] == chord and len(c) > n for c in self.modes.get(mode, {}))

    def under_prefix(self, mode: str, chord: Chord) -> list[Binding]:
        """Bindings that continue `chord` — what which-key shows."""
        n = len(chord)
        return sorted(
            (b for c, b in self.modes.get(mode, {}).items() if c[:n] == chord and len(c) > n),
            key=lambda b: b.keys,
        )

    def for_command(self, mode: str, cmd: str) -> str | None:
        """The keys bound to a command, for help and the hint bar."""
        return next((b.keys for b in self.modes.get(mode, {}).values() if b.cmd == cmd), None)

    def conflicts(self) -> list[str]:
        """Human-readable descriptions of every prefix collision, empty if clean."""
        out = []
        for mode, bindings in self.modes.items():
            for chord in bindings:
                for other in bindings:
                    if other != chord and other[: len(chord)] == chord:
                        out.append(
                            f"[modes.{mode}] {' '.join(chord)!r} shadows {' '.join(other)!r} "
                            f"({bindings[chord].cmd} vs {bindings[other].cmd})"
                        )
        return sorted(out)


def normalize(key: str, character: str | None = None) -> str:
    """A Textual key event as the token keys.toml uses.

    Printable characters bind as themselves (`?`, `\\`, `1`); everything else
    uses Textual's key name (`ctrl+d`, `escape`, `shift+tab`).
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
    """One keys.toml table as chords, `None` for a key set to `false`. Unknown
    commands are collected, not fatal."""
    out: dict[Chord, Binding | None] = {}
    for keys, spec in bindings.items():
        chord = tuple(normalize(k) for k in keys.split(" ") if k)
        if spec is False:
            out[chord] = None
            continue
        if not isinstance(spec, dict) or "cmd" not in spec:
            raise ValueError(
                f'{path}: [{section}] {keys!r} needs a cmd, like {{ cmd = "nav.down" }}, or false to unbind it'
            )
        cmd = spec["cmd"]
        registered = commands.REGISTRY.get(cmd)
        if registered is None:
            unknown.append(f"[{section}] {keys!r} -> unknown command {cmd!r}")
        out[chord] = Binding(
            chord=chord,
            cmd=cmd,
            args=spec.get("args", {}),
            desc=spec.get("desc") or (registered.desc if registered else ""),
        )
    return out


def _collide(a: Chord, b: Chord) -> bool:
    """The same key, or one a prefix of the other: one mode cannot bind both."""
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def _over(
    top: dict[Chord, Binding | None], under: dict[Chord, Binding | None], *, same_command: bool = False
) -> dict[Chord, Binding | None]:
    """`top` laid over `under`: an entry of `under` survives only if it collides
    with nothing in `top` — and, with `same_command`, if it is not the one key
    `under` has for a command `top` binds with the same args."""
    taken = [(b.cmd, b.args) for b in top.values() if b is not None] if same_command else []

    def moved(b: Binding | None) -> bool:
        # A command with several keys keeps them all: those are aliases, not a home to move.
        if b is None or (b.cmd, b.args) not in taken:
            return False
        return sum((o.cmd, o.args) == (b.cmd, b.args) for o in under.values() if o is not None) == 1

    kept = {chord: b for chord, b in under.items() if not any(_collide(chord, c) for c in top) and not moved(b)}
    return {**kept, **top}


def _modes(data: dict[str, Any], path: Path, unknown: list[str]) -> dict[str, dict[Chord, Binding | None]]:
    """One keys.toml as per-mode tables, `[global]` laid under each pane's own,
    which wins on the same key and on a shared prefix: a pane's `g` drops the
    global `g o` in that pane only, rather than a conflict refusing to start."""
    shared = _table("global", data.get("global", {}), path, unknown)
    tables = {
        mode: _table(f"modes.{mode}", bindings, path, unknown) for mode, bindings in data.get("modes", {}).items()
    }
    for mode in PANES:
        tables[mode] = _over(tables.get(mode, {}), shared)
    return tables


def load(path: Path | None = None) -> Keymap:
    """The shipped keymap with the user's keys.toml laid over it, key by key.

    A user binding drops every shipped binding it would collide with in the
    modes it reaches: the same key, a chord one is a prefix of, or the only key
    the mode has for the same command with the same args — so moving a command
    moves it, swapping two keys takes two lines, and aliases (`enter` beside
    `o`) stay unless unbound. `"o" = false` drops `o` (and everything under it,
    for a prefix) without binding anything. The conflict check is left with the
    collisions inside the user's own tables.

    `[global]` is laid under every pane's own table: one copy of the pane keys
    rather than one per mode, which is how a mode once ended up with none and
    trapped the keyboard.
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
        merged = _over(theirs.get(mode, {}), ours.get(mode, {}), same_command=True)
        modes[mode] = {chord: b for chord, b in merged.items() if b is not None}
    options = {**shipped.get("options", {}), **user.get("options", {})}
    return Keymap(modes, options, tuple(sorted(unknown)))
