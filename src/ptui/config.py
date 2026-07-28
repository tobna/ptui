"""Config loading: shipped defaults, overridden per key by the user's TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import papis.config

DEFAULTS_DIR = Path(__file__).parent / "defaults"


def config_dir() -> Path:
    """User config dir, `$PAPIS_CONFIG_DIR/ptui` (usually `~/.config/papis/ptui`)."""
    return Path(papis.config.get_config_folder()) / "ptui"


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge tables; anything else (including lists) is replaced wholesale."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _unknown(base: dict[str, Any], over: dict[str, Any], prefix: str = "") -> list[str]:
    """Dotted keys present in `over` but not in `base`. Does not descend into lists."""
    bad = []
    for key, value in over.items():
        dotted = f"{prefix}{key}"
        if key not in base:
            bad.append(dotted)
        elif isinstance(value, dict) and isinstance(base[key], dict):
            bad += _unknown(base[key], value, f"{dotted}.")
    return bad


@dataclass(frozen=True, slots=True)
class Config:
    data: dict[str, Any]
    unknown: tuple[str, ...]
    """Dotted keys the user set that ptui does not know — reported by app.config_check."""
    path: Path | None
    """The user's config.toml, or None if they have none."""

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def as_path(self, dotted: str) -> Path | None:
        """A config value as an expanded absolute path, or None if unset."""
        value = self.get(dotted)
        return Path(value).expanduser() if value else None

    def papis(self, dotted: str, papis_key: str) -> Any:
        """Our value if set, else the value papis already owns."""
        return self.get(dotted) or papis.config.get(papis_key)

    @property
    def theme_dirs(self) -> list[Path]:
        return [config_dir() / "themes", DEFAULTS_DIR / "themes"]


def load(path: Path | None = None) -> Config:
    """Load defaults + user overrides. `path` defaults to `<config_dir>/config.toml`."""
    defaults = tomllib.loads((DEFAULTS_DIR / "config.toml").read_text())
    if path is None:
        path = config_dir() / "config.toml"
    if not path.is_file():
        return Config(defaults, (), None)

    user = tomllib.loads(path.read_text())
    return Config(_merge(defaults, user), tuple(_unknown(defaults, user)), path)
