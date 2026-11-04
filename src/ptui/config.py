"""Config loading: shipped defaults, overridden per key by the user's TOML."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import papis.config

DEFAULTS_DIR = Path(__file__).parent / "defaults"


def config_dir() -> Path:
    """Find the user's config folder.

    It is `ptui/` in papis's config folder, which honours `$PAPIS_CONFIG_DIR` (on Linux usually
    `~/.config/papis/ptui`).

    Returns:
        The folder's path; it may not exist.
    """
    return Path(papis.config.get_config_folder()) / "ptui"


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge tables; anything else (including lists) is replaced wholesale.

    Args:
        base: The table to start from; left unchanged.
        over: The table whose values win.

    Returns:
        A new table holding both.
    """
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


USER_TABLES = ("query.aliases", "query.saved", "files.kind_patterns", "add.sources")
"""Tables whose keys the user names: a key the shipped table lacks is not unknown."""


def _unknown(base: dict[str, Any], over: dict[str, Any], prefix: str = "") -> list[str]:
    """Find the dotted keys present in `over` but not in `base`.

    Does not descend into lists or `USER_TABLES`.

    Args:
        base: The shipped table.
        over: The user's table.
        prefix: Dotted path of these tables, ending in a dot; empty at the top level.

    Returns:
        Dotted key names, in the order `over` holds them.
    """
    bad = []
    for key, value in over.items():
        dotted = f"{prefix}{key}"
        if key not in base:
            bad.append(dotted)
        elif isinstance(value, dict) and isinstance(base[key], dict) and dotted not in USER_TABLES:
            bad += _unknown(base[key], value, f"{dotted}.")
    return bad


@dataclass(frozen=True, slots=True)
class Config:
    """Shipped defaults merged with the user's overrides.

    Attributes:
        data: The merged settings, nested by TOML table.
        unknown: Dotted keys the user set that ptui does not know.
        path: The user's `config.toml`, or `None` if they have none.
        invalid: Sentences naming the values put back to the shipped default.
    """

    data: dict[str, Any]
    unknown: tuple[str, ...]
    """Dotted keys the user set that ptui does not know — reported by app.config_check."""
    path: Path | None
    """The user's config.toml, or None if they have none."""
    invalid: tuple[str, ...] = ()
    """Values that could not work and were replaced by the shipped default, as
    sentences — logged at startup and by app.config_check."""

    def get(self, dotted: str, default: Any = None) -> Any:
        """Look a setting up by its dotted key.

        Args:
            dotted: Dotted key, such as `ui.icons`.
            default: What to return when the key is missing.

        Returns:
            The stored value, which may be a whole table, or `default`.
        """
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def as_path(self, dotted: str) -> Path | None:
        """A config value as an expanded absolute path, or None if unset.

        Args:
            dotted: Dotted key of a path setting.

        Returns:
            The path with `~` expanded, or `None` when the setting is unset or empty.
        """
        value = self.get(dotted)
        return Path(value).expanduser() if value else None

    def papis(self, dotted: str, papis_key: str) -> Any:
        """Our value if set, else the value papis already owns.

        Args:
            dotted: Dotted key of ptui's own setting.
            papis_key: Name of the papis setting to fall back on.

        Returns:
            ptui's value when it is truthy, else papis's.
        """
        return self.get(dotted) or papis.config.get(papis_key)


def load(path: Path | None = None) -> Config:
    """Load the shipped defaults and the user's overrides.

    Args:
        path: The user's config file; `None` means `<config_dir>/config.toml`.

    Returns:
        The merged, repaired `Config`.

    Raises:
        ValueError: When the user's file is not valid TOML.
    """
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

    Args:
        data: The merged settings, nested by TOML table; repaired in place.

    Returns:
        One sentence per value that was put back or left out, empty when all were fine.
    """
    defaults = tomllib.loads((DEFAULTS_DIR / "config.toml").read_text())  # fresh: `data` may share its tables
    problems = []

    def tables(base: dict[str, Any], over: dict[str, Any], prefix: str = "") -> None:
        """Put back any shipped table the user replaced with something else.

        Args:
            base: The shipped table.
            over: The same table in the merged settings; repaired in place.
            prefix: Dotted path of these tables, ending in a dot; empty at the top level.
        """
        # `ui = "dark"`: every lookup below would index into a string and raise TypeError
        for key, default in base.items():
            if not isinstance(default, dict):
                continue
            if isinstance(over.get(key), dict):
                tables(default, over[key], f"{prefix}{key}.")
            else:
                problems.append(f"{prefix}{key} = {over.get(key)!r} is not a table; using the shipped one")
                over[key] = default

    tables(defaults, data)

    def replace(dotted: str, value: Any, why: str) -> None:
        """Put the shipped default back for one key and record why.

        Args:
            dotted: Dotted key to repair.
            value: The value that cannot work.
            why: The rest of the sentence, saying what is wrong with it.
        """
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

    sources = data["add"]["sources"]
    for name, source in list(sources.items()):
        if isinstance(source, dict) and all(isinstance(source.get(field), str) for field in ("label", "example")):
            continue
        shipped = defaults["add"]["sources"].get(name)
        problems.append(
            f"add.sources.{name} = {source!r} needs a label and an example; "
            + ("using the shipped one" if shipped else "left out")
        )
        if shipped:
            sources[name] = shipped
        else:
            del sources[name]
    return problems


def read_toml(path: Path) -> dict[str, Any]:
    """Read a user's TOML file.

    A syntax error names the file: tomllib only gives the line and column, and the user has two files
    that could hold it.

    Args:
        path: The file to read.

    Returns:
        The parsed tables.

    Raises:
        ValueError: When the file is not valid TOML.
    """
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{path}: {error}") from error
