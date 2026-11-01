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


def _table(section: str, bindings: dict[str, Any], path: Path, unknown: list[str]) -> dict[Chord, Binding]:
    """One keys.toml table as chords. Unknown commands are collected, not fatal."""
    out: dict[Chord, Binding] = {}
    for keys, spec in bindings.items():
        chord = tuple(normalize(k) for k in keys.split(" ") if k)
        if not isinstance(spec, dict) or "cmd" not in spec:
            raise ValueError(f'{path}: [{section}] {keys!r} needs a cmd, like {{ cmd = "nav.down" }}')
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


def load(path: Path | None = None) -> Keymap:
    """Load shipped defaults, then the user's keys.toml — per table, replacing wholesale.

    `[global]` is laid under every pane's own table, which wins on the same keys:
    one copy of the pane keys rather than one per mode, which is how a mode once
    ended up with none and trapped the keyboard.
    """
    data = tomllib.loads((DEFAULTS_DIR / "keys.toml").read_text())
    if path is None:
        path = config_dir() / "keys.toml"
    if path.is_file():
        user = read_toml(path)
        data["options"] = {**data.get("options", {}), **user.get("options", {})}
        # A table the user defines replaces ours: partial merges make it impossible
        # to remove a shipped binding.
        data["modes"] = {**data.get("modes", {}), **user.get("modes", {})}
        data["global"] = user.get("global", data.get("global", {}))

    unknown: list[str] = []
    shared = _table("global", data.get("global", {}), path, unknown)
    modes = {mode: _table(f"modes.{mode}", bindings, path, unknown) for mode, bindings in data["modes"].items()}
    for mode in PANES:
        modes[mode] = {**shared, **modes.get(mode, {})}
    return Keymap(modes, data.get("options", {}), tuple(sorted(unknown)))
