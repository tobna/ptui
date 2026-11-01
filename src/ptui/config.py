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
    invalid: tuple[str, ...] = ()
    """Values that could not work and were replaced by the shipped default, as
    sentences — logged at startup and by app.config_check."""

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


def load(path: Path | None = None) -> Config:
    """Load defaults + user overrides. `path` defaults to `<config_dir>/config.toml`."""
    defaults = tomllib.loads((DEFAULTS_DIR / "config.toml").read_text())
    if path is None:
        path = config_dir() / "config.toml"
    if not path.is_file():
        return Config(defaults, (), None)

    user = read_toml(path)
    data = _merge(defaults, user)
    return Config(data, tuple(_unknown(defaults, user)), path, tuple(repair(data)))


CHOICES = {
    "ui.layout": ("auto", "vertical", "horizontal"),
    "query.narrow_mode": ("substring", "fuzzy", "regex"),
    "list.null_ordering": ("last", "first"),
    "undo.strategy": ("trash", "git", "none"),
    "edit.mode": ("editor", "structured"),
    "files.collision": ("skip", "suffix", "ask"),
    "export.clipboard": ("auto", "wl-copy", "xclip", "pbcopy", "osc52"),
    "log.level": ("trace", "debug", "info", "success", "warning", "error", "critical"),
}
"""ptui's own enumerations — papis's settings are papis's to check."""


def repair(data: dict[str, Any]) -> list[str]:
    """Put the shipped default back wherever a value cannot work, and say so.

    Left alone, a bad value either crashed at startup (`log.level = "loud"`, a
    column with no `width`) or quietly meant something else (`layout =
    "diagonal"` read as side by side, `split_ratio = 1.5` pushed the list off
    the screen). Also run on every `config.set`.
    """
    defaults = tomllib.loads((DEFAULTS_DIR / "config.toml").read_text())  # fresh: `data` may share its tables
    problems = []

    def replace(dotted: str, value: Any, why: str) -> None:
        section, _, key = dotted.rpartition(".")
        default = Config(defaults, (), None).get(dotted)
        Config(data, (), None).get(section)[key] = default
        problems.append(f"{dotted} = {value!r} {why}; using {default!r}")

    view = Config(data, (), None)
    for dotted, allowed in CHOICES.items():
        if str(value := view.get(dotted)).casefold() not in allowed:
            replace(dotted, value, f"is not one of {', '.join(allowed)}")
        else:  # the code compares exactly: `"Horizontal"` would pass here and read as side by side
            section, _, key = dotted.rpartition(".")
            view.get(section)[key] = value.casefold()
    ratio = view.get("ui.split_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, int | float) or not 0.1 <= ratio <= 0.9:
        replace("ui.split_ratio", ratio, "is not between 0.1 and 0.9")
    height = view.get("list.row_height")
    if isinstance(height, bool) or not isinstance(height, int) or height < 1:
        replace("list.row_height", height, "is not a whole number of lines")

    columns = []
    for column in view.get("list.columns") or []:
        if not isinstance(column, dict) or not isinstance(column.get("format"), str):
            problems.append(f"list.columns: {column!r} has no format; left out")
            continue
        width = column.setdefault("width", 0)  # missing means flexible, as 0 does
        if isinstance(width, bool) or not isinstance(width, int) or width < 0:
            problems.append(f"list.columns: {column.get('title', '')!r} width = {width!r} is not a cell count; using 0")
            column["width"] = 0
        column.setdefault("title", "")
        columns.append(column)
    if not columns:
        problems.append("list.columns is empty; using the shipped columns")
        columns = defaults["list"]["columns"]
    data["list"]["columns"] = columns
    return problems


def read_toml(path: Path) -> dict[str, Any]:
    """A user's TOML file. A syntax error names the file: tomllib only gives the
    line and column, and the user has two files that could hold it."""
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: {exc}") from exc
