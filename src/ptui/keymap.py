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
from ptui.config import DEFAULTS_DIR, config_dir

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


def load(path: Path | None = None) -> Keymap:
    """Load shipped defaults, then the user's keys.toml — per mode, replacing wholesale."""
    data = tomllib.loads((DEFAULTS_DIR / "keys.toml").read_text())
    if path is None:
        path = config_dir() / "keys.toml"
    if path.is_file():
        user = tomllib.loads(path.read_text())
        data["options"] = {**data.get("options", {}), **user.get("options", {})}
        # A mode the user defines replaces ours: partial merges make it impossible
        # to remove a shipped binding.
        data["modes"] = {**data.get("modes", {}), **user.get("modes", {})}

    modes: dict[str, dict[Chord, Binding]] = {}
    unknown: list[str] = []
    for mode, bindings in data.get("modes", {}).items():
        modes[mode] = {}
        for keys, spec in bindings.items():
            chord = tuple(normalize(k) for k in keys.split(" ") if k)
            cmd = spec["cmd"]
            registered = commands.REGISTRY.get(cmd)
            if registered is None:
                unknown.append(f"[modes.{mode}] {keys!r} -> unknown command {cmd!r}")
            modes[mode][chord] = Binding(
                chord=chord,
                cmd=cmd,
                args=spec.get("args", {}),
                desc=spec.get("desc") or (registered.desc if registered else ""),
            )
    return Keymap(modes, data.get("options", {}), tuple(sorted(unknown)))
