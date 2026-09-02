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
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, SelectionList, Static

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
    # Ratings, drawn as five cells. The ASCII pair has to be as wide as the
    # nerd one, so it is asterisk and middle dot rather than "3/5".
    "star": ("*", "\uf005"),  # nf-fa-star
    "star_empty": ("\u00b7", "\uf006"),  # nf-fa-star_o
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


def literal(text: str) -> str:
    """Text that must reach the screen exactly as it is, inside Textual markup.

    **Not `rich.markup.escape`, and not `textual.markup.escape`** — both only
    escape what *looks* like a tag, and Textual's renderer drops far more than
    that: a title reading `Attention \\[Extended]` renders as `Attention` with
    the bracketed word gone. Escaping every `[` is the only thing that survives
    `[Extended]`, `[doc[year]]` and `[bold]` alike. Measured, all three.
    """
    return text.replace("[", "\\[")


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
        return library.match_text(f"{self.label} {self.hint} {self.haystack}", library.parse_query(needle))


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
                options.highlighted = max(0, min(options.option_count - 1, (options.highlighted or 0) + step))
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


@dataclass(frozen=True, slots=True)
class FileChoice:
    """One `files` entry in the delete dialog, with why it is (un)checked."""

    label: str
    checked: bool
    note: str = ""


class ConfirmDelete(ModalScreen[list[int] | None]):
    """Delete N documents: which of their files go too.

    Dismisses with the indices of the checked files, or None when cancelled —
    an empty list is a real answer ("the documents, none of the files") and is
    why this cannot dismiss with a bare bool.
    """

    # priority: the list itself binds `enter` to toggling, and confirming has
    # to win. `space` stays the toggle, which is what the list already does.
    BINDINGS: ClassVar = [
        Binding("enter", "confirm", "delete", priority=True),
        Binding("escape", "cancel", "cancel", priority=True),
    ]

    DEFAULT_CSS = """
    ConfirmDelete { align: center middle; background: $background 60%; }
    ConfirmDelete > Vertical {
        width: 70%; max-width: 100; height: auto; max-height: 80%;
        border: round $error; background: $surface;
    }
    ConfirmDelete #picker-title {
        padding: 0 1; text-style: bold; background: $error; color: $background;
    }
    ConfirmDelete #delete-docs { padding: 0 1; }
    ConfirmDelete SelectionList { height: auto; max-height: 12; border: none; padding: 0 1; }
    ConfirmDelete #delete-keys { padding: 0 1; color: $text-muted; }
    """

    def __init__(self, summary: list[str], files: list[FileChoice], *, title: str) -> None:
        super().__init__()
        self.summary = summary
        self.files = files
        self.title_text = title

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title_text, id="picker-title")
            yield Static("\n".join(self.summary), id="delete-docs")
            if self.files:
                yield SelectionList[int](
                    *(
                        (
                            f"{choice.label}{f'  [dim]{choice.note}[/]' if choice.note else ''}",
                            index,
                            choice.checked,
                        )
                        for index, choice in enumerate(self.files)
                    )
                )
            yield Static(
                "[$accent]space[/] toggle a file   [$accent]enter[/] delete   [$accent]escape[/] cancel",
                id="delete-keys",
            )

    def on_mount(self) -> None:
        if self.files:
            self.query_one(SelectionList).focus()

    def action_confirm(self) -> None:
        self.dismiss(list(self.query_one(SelectionList).selected) if self.files else [])

    def action_cancel(self) -> None:
        self.dismiss(None)


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
        border: round $primary; background: $surface; padding: 0 0 1 0;
    }
    /* The same header bar `SelectList` wears: one modal look, not two. */
    AddForm #picker-title {
        padding: 0 1; text-style: bold; background: $primary; color: $background;
    }
    /* The focused field is the only one with a background, which is what makes
       a stack of six inputs readable as "you are typing in this one". */
    /* `height: 1` matters: an `Input` is three rows tall by default, so six of
       them plus labels ran off the bottom of an 80x24 terminal. */
    AddForm Input, AddForm Input:focus {
        height: 1; border: none; padding: 0 1; margin: 0 1; background: $surface;
    }
    AddForm Input:focus { background: $panel; }
    AddForm .field-label { color: $text-muted; padding: 0 2; }
    AddForm #add-preview { padding: 1 2 0 2; color: $text-muted; }
    """

    def __init__(self, source: Path | None, data: dict[str, str], preview: Callable[[dict], str]) -> None:
        super().__init__()
        self.source = source
        """None for a metadata-only add — an importer that found no PDF."""
        self.data = data
        self.preview = preview

    def compose(self) -> ComposeResult:
        with Vertical():
            what = self.source.name if self.source else "from metadata"
            # `literal`: a file name may hold brackets, and this is the one
            # string here that goes through markup.
            yield Static(f"Add {literal(what)}", id="picker-title")
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
