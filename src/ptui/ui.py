"""`SelectList` — the one modal picker, shared by every "choose one of these".

Built once and reused by the sort picker, `files.open_pick` and `lib.switch`
(and, later, saved searches and doctor findings).

Deviation from SPEC, deliberate: navigation is `up`/`down` (plus `ctrl+n` /
`ctrl+p`), not `j`/`k`. The filter box has focus so that fuzzy filtering happens
as you type, and a key cannot be both a letter and a motion.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from ptui import keymap, library

GLYPHS = {
    #  name             ASCII   nerd font
    "mark": ("*", ""),  # nf-fa-check
    "file": ("·", ""),  # nf-fa-file_text
    "file_missing": ("!", ""),  # nf-fa-warning
    "sort_desc": ("↓", ""),  # nf-fa-long_arrow_down
    "sort_asc": ("↑", ""),  # nf-fa-long_arrow_up
    "cursor": (">", ""),  # nf-fa-chevron_right
}
"""Every symbol ptui prints, ASCII first and nerd font second. Never emit one
directly: `ui.icons = false` is the shipped default because this runs over SSH
to a cluster, and a glyph written inline is a glyph that ignores the setting.
Both columns are one cell wide, so column arithmetic does not care which is on.
"""

# ponytail: one process, one font — a module global beats threading `ui.icons`
# through every call site. `use_icons` is called once, from `PtuiApp.__init__`.
_ICONS = False


def use_icons(enabled: bool) -> None:
    global _ICONS
    _ICONS = enabled


def glyph(name: str) -> str:
    return GLYPHS[name][_ICONS]


@dataclass(frozen=True, slots=True)
class Item:
    label: str
    value: Any
    hint: str = ""
    """Right-hand column: the sort key, the bound keys, a path — whatever teaches."""
    haystack: str = field(default="")

    def matches(self, needle: str) -> bool:
        return not needle or library.is_subsequence(
            needle.replace(" ", ""), f"{self.label} {self.hint} {self.haystack}".casefold()
        )


class SelectList(ModalScreen[tuple[Any, bool] | None]):
    """Dismisses with `(value, inverted)`, or None when cancelled."""

    DEFAULT_CSS = """
    SelectList { align: center middle; }
    SelectList > Vertical {
        width: 70%; max-width: 100; height: auto; max-height: 80%;
        border: round $accent; background: $surface;
    }
    SelectList #picker-title { padding: 0 1; text-style: bold; }
    SelectList Input { border: none; padding: 0 1; }
    SelectList OptionList { height: auto; max-height: 20; border: none; }
    """

    def __init__(self, items: list[Item], *, title: str, current: Any = None) -> None:
        super().__init__()
        self.items = items
        self.title_text = title
        self.current = current
        self.shown: list[Item] = list(items)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title_text, id="picker-title")
            yield Input(placeholder="filter")
            yield OptionList()

    def on_mount(self) -> None:
        self._populate()
        index = next((i for i, it in enumerate(self.shown) if it.value == self.current), 0)
        self.query_one(OptionList).highlighted = index
        self.query_one(Input).focus()

    def _populate(self) -> None:
        options = self.query_one(OptionList)
        options.clear_options()
        for item in self.shown:
            marker = glyph("cursor") if item.value == self.current else " "
            hint = f"  [dim]{item.hint}[/]" if item.hint else ""
            options.add_option(f"{marker} {item.label}{hint}")
        options.highlighted = 0 if self.shown else None

    def on_input_changed(self, event: Input.Changed) -> None:
        needle = event.value.casefold()
        self.shown = [item for item in self.items if item.matches(needle)]
        self._populate()

    def on_key(self, event: Any) -> None:
        """Dispatch through `[modes.picker]` — the same table as everything else."""
        token = keymap.normalize(event.key, event.character)
        binding = self.app.km.lookup("picker", (token,))
        if binding is None:
            return  # anything else is filter text
        event.stop()

        options = self.query_one(OptionList)
        if binding.cmd in ("nav.down", "nav.up"):
            step = binding.args.get("count", 1) * (1 if binding.cmd == "nav.down" else -1)
            if options.option_count:
                options.highlighted = max(
                    0, min(options.option_count - 1, (options.highlighted or 0) + step)
                )
        elif binding.cmd == "picker.confirm":
            index = options.highlighted
            picked = self.shown[index] if index is not None and self.shown else None
            self.dismiss((picked.value, binding.args.get("invert", False)) if picked else None)
        elif binding.cmd == "picker.cancel":
            self.dismiss(None)


def pick(app: Any, items: list[Item], *, title: str, current: Any = None) -> Callable[..., Any]:
    """`app.push_screen(SelectList(...), on_confirm)`, minus the ceremony.

    Usage: `ui.pick(app, items, title="Sort by")(lambda value, inverted: ...)`.
    """

    def run(on_confirm: Callable[[Any, bool], None]) -> None:
        def handle(result: tuple[Any, bool] | None) -> None:
            if result is not None:
                on_confirm(*result)

        app.push_screen(SelectList(items, title=title, current=current), handle)

    return run


# ponytail: a fixed field list. `edit.structured_fields` belongs to the
# structured editor (post-v0), and `author_list` is not a one-line text field.
ADD_FIELDS = ("title", "author", "year", "ref", "tags", "doi")


class AddForm(ModalScreen[dict[str, str] | None]):
    """Metadata *before* the name is computed, with a live destination preview.

    Nothing touches the disk until this screen is confirmed.
    """

    DEFAULT_CSS = """
    AddForm { align: center middle; }
    AddForm > Vertical {
        width: 80%; max-width: 110; height: auto;
        border: round $accent; background: $surface; padding: 0 1;
    }
    AddForm Input { border: none; padding: 0; }
    AddForm .field-label { color: $text-muted; }
    AddForm #add-preview { padding: 1 0 0 0; }
    """

    def __init__(self, source: Path, data: dict[str, str], preview: Callable[[dict], str]) -> None:
        super().__init__()
        self.source = source
        self.data = data
        self.preview = preview

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Add [bold]{self.source.name}[/]", id="picker-title")
            for field_name in ADD_FIELDS:
                yield Static(field_name, classes="field-label")
                yield Input(value=str(self.data.get(field_name, "")), id=f"add-{field_name}")
            # markup=False: a destination path is full of `[doc[year]]`-shaped
            # text that Textual would otherwise read as markup.
            yield Static(id="add-preview", markup=False)

    def on_mount(self) -> None:
        self._refresh_preview()
        self.query(Input).first().focus()

    def _values(self) -> dict[str, str]:
        return {
            name: self.query_one(f"#add-{name}", Input).value.strip()
            for name in ADD_FIELDS
            if self.query_one(f"#add-{name}", Input).value.strip()
        }

    def _refresh_preview(self) -> None:
        self.query_one("#add-preview", Static).update(self.preview(self._values()))

    def on_input_changed(self, _: Input.Changed) -> None:
        self._refresh_preview()

    def on_input_submitted(self, _: Input.Submitted) -> None:
        self.dismiss(self._values())

    def on_key(self, event: Any) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()
