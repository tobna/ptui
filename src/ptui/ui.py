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
    "warning": ("!", ""),  # nf-fa-warning — every warning, everywhere
    # Same in both columns on purpose: the nerd long-arrows (U+F175/F176) are
    # no clearer than a plain arrow and fall back badly where they are missing.
    "sort_desc": ("↓", "↓"),
    "sort_asc": ("↑", "↑"),
    # Powerline separators for the status bar. No ASCII twin exists — a ">"
    # between two coloured blocks reads as text, so the fallback is a space
    # and the blocks simply butt up against each other.
    "sep_right": (" ", "\ue0b0"),
    "sep_left": (" ", "\ue0b2"),
    "cursor": (">", ""),  # nf-fa-chevron_right
    "scope": (" ", ""),  # nf-fa-search
    # Document kinds, keyed by `library.kind` - the `type` field, except that an
    # arXiv-only article reads as `preprint`. `type.misc` is the fallback for
    # anything unrecognised, so a library with `incollection` still renders.
    "type.inproceedings": ("C", ""),  # nf-fa-users
    # Both columns: the chi of the arXiv wordmark needs no patched font.
    "type.preprint": ("χ", "χ"),
    "type.article": ("A", ""),  # nf-fa-file_text_o
    "type.book": ("B", ""),  # nf-fa-book
    "type.thesis": ("T", ""),  # nf-fa-graduation_cap
    "type.online": ("W", ""),  # nf-fa-globe
    "type.report": ("R", ""),  # nf-fa-file_text
    "type.misc": ("?", ""),  # nf-fa-question
    # Info-pane field labels. The ASCII column is a space: the field name is
    # written out beside it, so there is nothing for a substitute to say.
    "field.author": (" ", ""),  # nf-fa-user
    "field.year": (" ", ""),  # nf-fa-calendar
    "field.ref": (" ", ""),  # nf-fa-key
    "field.doi": (" ", ""),  # nf-fa-link
    "field.url": (" ", ""),  # nf-fa-globe
    "field.tags": (" ", ""),  # nf-fa-tags
    "field.venue": (" ", ""),  # nf-fa-university
    "field.notes": (" ", ""),  # nf-fa-pencil
    "field.reading_status": (" ", ""),  # nf-fa-bookmark
    "field.rating": (" ", ""),  # nf-fa-star
    "field.files": (" ", ""),  # nf-fa-file_text
    "field.doctor": (" ", ""),  # nf-fa-stethoscope
}
"""Every symbol ptui prints, ASCII first and nerd font second. Never emit one
directly: a glyph written inline is a glyph that ignores `ui.icons`, and the
ASCII column is what a bare tty over SSH has. Both columns are one cell wide,
so column arithmetic does not care which is on — a slot with no sensible ASCII
twin uses a space rather than a two-character abbreviation.
"""

# ponytail: one process, one font — a module global beats threading `ui.icons`
# through every call site. `use_icons` is called once, from `PtuiApp.__init__`.
_ICONS = False


def use_icons(enabled: bool) -> None:
    global _ICONS
    _ICONS = enabled


def glyph(name: str, fallback: str = "") -> str:
    """`fallback` is the name of another glyph, for families keyed by data:
    `glyph(f"type.{doc['type']}", "type.misc")` survives an unexpected type.
    Without one a missing name raises, which is what a typo deserves."""
    return GLYPHS[name if name in GLYPHS or not fallback else fallback][_ICONS]


@dataclass(frozen=True, slots=True)
class Item:
    label: str
    value: Any
    hint: str = ""
    """Right-hand column: the sort key, the bound keys, a path — whatever teaches."""
    haystack: str = field(default="")

    def matches(self, needle: str) -> bool:
        """The same matcher the list uses, so `f o` and `/` behave alike. It has
        to be: a subsequence test over a long file name matched almost anything."""
        return library.match_text(
            f"{self.label} {self.hint} {self.haystack}", library.parse_query(needle)
        )


class SelectList(ModalScreen[tuple[Any, bool] | None]):
    """Dismisses with `(value, inverted)`, or None when cancelled."""

    DEFAULT_CSS = """
    SelectList { align: center middle; background: $background 60%; }
    SelectList > Vertical {
        width: 70%; max-width: 100; height: auto; max-height: 80%;
        border: round $primary; background: $surface;
    }
    SelectList #picker-title {
        padding: 0 1; text-style: bold; background: $primary; color: $background;
    }
    SelectList Input, SelectList Input:focus { border: none; padding: 0 1; background: $surface; }
    SelectList OptionList { height: auto; max-height: 20; border: none; background: $surface; }
    SelectList OptionList > .option-list--option-highlighted {
        background: $primary 30%; text-style: bold;
    }
    """

    def __init__(self, items: list[Item], *, title: str, current: Any = None) -> None:
        super().__init__()
        self.items = items
        self.title_text = title
        self.current = current
        self.shown: list[Item] = list(items)
        self._cursor_at = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title_text, id="picker-title")
            yield Input(placeholder="filter")
            yield OptionList()

    def on_mount(self) -> None:
        self._populate()
        index = next((i for i, it in enumerate(self.shown) if it.value == self.current), 0)
        options = self.query_one(OptionList)
        options.highlighted = index
        # after the refresh: at mount time the list has no geometry yet, so the
        # scroll lands nowhere and a picker opened on a far-down current value
        # (21 themes, one library) shows the top of the list instead
        self.call_after_refresh(options.scroll_to_highlight)
        self.query_one(Input).focus()

    def _prompt(self, item: Item, *, here: bool) -> str:
        """The cursor points at the row you are on — every picker has one of those,
        while only some are opened with a `current` value. That one is marked by
        being bold, not by the cursor, or the two would fight for the same cell."""
        label = f"[bold]{item.label}[/]" if item.value == self.current else item.label
        hint = f"  [dim]{item.hint}[/]" if item.hint else ""
        return f"{glyph('cursor') if here else ' '} {label}{hint}"

    def _populate(self) -> None:
        options = self.query_one(OptionList)
        options.clear_options()
        for index, item in enumerate(self.shown):
            options.add_option(self._prompt(item, here=index == 0))
        options.highlighted = 0 if self.shown else None

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Move the cursor glyph with the highlight — two rows change, not the list."""
        options = self.query_one(OptionList)
        for index in {self._cursor_at, event.option_index}:
            if 0 <= index < len(self.shown):
                options.replace_option_prompt_at_index(
                    index, self._prompt(self.shown[index], here=index == event.option_index)
                )
        self._cursor_at = event.option_index

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
    AddForm { align: center middle; background: $background 60%; }
    AddForm > Vertical {
        width: 80%; max-width: 110; height: auto;
        border: round $primary; background: $surface; padding: 0 1;
    }
    AddForm Input { border: none; padding: 0; }
    AddForm .field-label { color: $text-muted; }
    AddForm #add-preview { padding: 1 0 0 0; color: $text-muted; }
    """

    def __init__(
        self, source: Path | None, data: dict[str, str], preview: Callable[[dict], str]
    ) -> None:
        super().__init__()
        self.source = source
        """None for a metadata-only add — an importer that found no PDF."""
        self.data = data
        self.preview = preview

    def compose(self) -> ComposeResult:
        with Vertical():
            what = self.source.name if self.source else "from metadata"
            yield Static(f"Add [bold]{what}[/]", id="picker-title")
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
