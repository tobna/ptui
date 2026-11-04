"""Generate `CONFIG.md` from the shipped `config.toml`.

The commented defaults are already the reference for every setting; this
renders them as tables, so the documentation cannot drift from what ptui
ships. `tests/test_docs.py` fails when the file is stale.

    uv run python scripts/configdoc.py          # rewrite CONFIG.md
    uv run python scripts/configdoc.py --check  # print it instead
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ptui.config import CHOICES, DEFAULTS_DIR

ROOT = Path(__file__).resolve().parent.parent
HEADER = re.compile(r"^(\[\[?)([\w.-]+)\]\]?$")

INTRO = """\
# Configuration

ptui reads `config.toml` from the `ptui` folder in papis's config folder,
usually `~/.config/papis/ptui/`. The file does not have to exist: write only
the settings you want to change. Key bindings go in `keys.toml` next to it, and
[KEYS.md](KEYS.md) lists them and their options.

Each setting you write replaces that one setting, and the rest keep their
defaults. A list of tables (`[[list.sort_presets]]`, `[[list.columns]]`,
`[[files.rules]]`) is the exception: yours replaces the shipped list as a
whole, so copy the shipped entries you want to keep.

papis still decides the libraries, the editor, `opentool` and `use-git`; a
setting here overrides papis's value for ptui only. A value that cannot work,
such as a layout that does not exist, falls back to its default with a line in
the log, and `\\ c` lists every setting ptui does not know or cannot use. A
TOML syntax error stops ptui at startup and names the file.
`:config.set list.row_height 2` tries a setting for the session without
writing the file.

This page is generated from
[`src/ptui/defaults/config.toml`](src/ptui/defaults/config.toml) by
`uv run python scripts/configdoc.py`.
"""


@dataclass(slots=True)
class Section:
    """One table of `config.toml`, as the page shows it.

    Attributes:
        name: The table's dotted name.
        array: It is a list of tables, shown as a TOML block.
        intro: The comment block above it, as prose.
        rows: `(key, default, note)` per setting of a plain table.
        lines: The raw lines of a list of tables.
    """

    name: str
    array: bool
    intro: str
    rows: list[tuple[str, str, str]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def split_value(rest: str) -> tuple[str, str]:
    """`value  # note` -> (value, note). A `#` inside a string stays in the value.

    Args:
        rest: What follows the `=` on a line.

    Returns:
        `(value, note)`, stripped; the note is empty when there is none.
    """
    for index, char in enumerate(rest):
        if char == "#":
            try:
                tomllib.loads(f"v = {rest[:index]}")
            except tomllib.TOMLDecodeError:
                continue
            return rest[:index].strip(), rest[index + 1 :].strip()
    return rest.strip(), ""


def paragraphs(comments: list[str]) -> str:
    """A comment block as prose: `#` alone breaks a paragraph, `# ----` rules go.

    Args:
        comments: The comment lines, without their `#`.

    Returns:
        The paragraphs, separated by blank lines.
    """
    blocks, current = [], []
    for comment in [*comments, ""]:
        if comment and set(comment) <= {"-"}:
            continue
        if comment:
            current.append(comment)
        elif current:
            blocks.append(" ".join(current))
            current = []
    return "\n\n".join(blocks)


def sections(text: str) -> list[Section]:
    """Parse `config.toml` into its tables, keeping the comments.

    Args:
        text: The file's text.

    Returns:
        One `Section` per table; repeated `[[...]]` headers fold into one.
    """
    found: list[Section] = []
    comments: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            comments = []  # a comment block introduces only the header right under it
            if found and found[-1].array:
                found[-1].lines.append("")
            continue
        if stripped.startswith("#"):
            comments.append(stripped.lstrip("#").strip())
            continue
        if header := HEADER.match(stripped):
            array, name = header[1] == "[[", header[2]
            if array and found and found[-1].array and found[-1].name == name:
                found[-1].lines.append(line)  # one block for the whole list
            else:
                found.append(Section(name, array, paragraphs(comments), lines=[line] if array else []))
        elif found[-1].array:
            found[-1].lines.append(line)
        else:
            key, _, rest = line.partition("=")
            value, note = split_value(rest)
            found[-1].rows.append((key.strip(), value, " ".join([*comments, note]).strip()))
        comments = []
    for section in found:
        while section.lines and not section.lines[-1]:
            section.lines.pop()
    return found


def cell(text: str) -> str:
    """Escape text for a Markdown table cell.

    Args:
        text: The cell's text.

    Returns:
        The text with `|` escaped.
    """
    return text.replace("|", "\\|")


def render() -> str:
    """Render `CONFIG.md` from the shipped `config.toml`.

    Returns:
        The whole file as text.
    """
    lines = [INTRO]
    for section in sections((DEFAULTS_DIR / "config.toml").read_text()):
        lines += [f"## `[[{section.name}]]`" if section.array else f"## `[{section.name}]`", ""]
        if section.intro:
            lines += [section.intro, ""]
        if section.array:
            lines += ["```toml", *section.lines, "```", ""]
            continue
        lines += ["| Setting | Default | What it does |", "| --- | --- | --- |"]
        for key, value, note in section.rows:
            choices = CHOICES.get(f"{section.name}.{key}", ())
            if not all(choice in note for choice in choices):
                note = f"{note}{'; ' if note else ''}one of {', '.join(f'`{option}`' for option in choices)}"
            lines.append(f"| `{key}` | `{cell(value)}` | {cell(note)} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        print(text)
    else:
        (ROOT / "CONFIG.md").write_text(text)
        print(f"wrote {ROOT / 'CONFIG.md'}")
