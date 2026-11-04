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

from loguru import logger
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
    "field.abstract": (" ", ""),  # nf-fa-quote_left
}
"""Every symbol ptui prints, ASCII first and nerd font second. Never emit one
directly: a glyph written inline is a glyph that ignores `ui.icons`, and the
ASCII column is what a bare tty over SSH has. Both columns are one cell wide,
so column arithmetic does not care which is on — a slot with no sensible ASCII
twin uses a space rather than a two-character abbreviation.
"""


def literal(text: str) -> str:
    r"""Text that must reach the screen exactly as it is, inside Textual markup.

    **Not `rich.markup.escape`, and not `textual.markup.escape`** — both only
    escape what *looks* like a tag, and Textual's renderer drops far more than
    that: a title reading `Attention \[Extended]` renders as `Attention` with
    the bracketed word gone. Escaping every `[` is the only thing that survives
    `[Extended]`, `[doc[year]]` and `[bold]` alike. Measured, all three.

    Args:
        text: The text to show.

    Returns:
        The text with every `[` escaped.
    """
    return text.replace("[", "\\[")


# ponytail: one process, one font — a module global beats threading `ui.icons`
# through every call site. `use_icons` is called once, from `PtuiApp.__init__`.
_ICONS = False


def use_icons(enabled: bool) -> None:
    """Choose between the ASCII and nerd-font column of `GLYPHS`.

    Args:
        enabled: `True` for the nerd-font column.
    """
    global _ICONS
    _ICONS = enabled


def glyph(name: str, fallback: str = "") -> str:
    """Look a symbol up in `GLYPHS`, in the column `use_icons` chose.

    `fallback` is the name of another glyph, for families keyed by data:
    `glyph(f"type.{doc['type']}", "type.misc")` survives an unexpected type. Without one a missing name
    raises, which is what a typo deserves.

    Args:
        name: The glyph's name.
        fallback: The name to use when `name` is unknown; empty for none.

    Returns:
        The symbol, one cell wide.

    Raises:
        KeyError: When neither name is known.
    """
    return GLYPHS[name if name in GLYPHS or not fallback else fallback][_ICONS]


@dataclass(frozen=True, slots=True)
class Item:
    """One row of a picker.

    Attributes:
        label: The text shown.
        value: What the picker answers with.
        hint: Right-hand column: the sort key, the bound keys, a path; whatever teaches.
        haystack: Extra text the filter searches but the row does not show.
    """

    label: str
    value: Any
    hint: str = ""
    """Right-hand column: the sort key, the bound keys, a path — whatever teaches."""
    haystack: str = field(default="")

    def matches(self, needle: str) -> bool:
        """Test the row against the picker's filter.

        The same matcher the list uses, so `f o` and `/` behave alike. It has to be: a subsequence test over
        a long file name matched almost anything.

        Args:
            needle: The filter as typed.

        Returns:
            `True` when every term hits the label, hint or haystack.
        """
        return library.match_text(f"{self.label} {self.hint} {self.haystack}", library.parse_query(needle))


class SelectList(ModalScreen[tuple[Any, bool] | None]):
    """Modal picker over a list of `Item`s, filtered as you type.

    Dismisses with `(value, inverted)`, or None when cancelled.
    """

    DEFAULT_CSS = """
    SelectList { align: center middle; background: $background 60%; }
    SelectList > Vertical {
        width: 70%; max-width: 100; height: auto; max-height: 80%;
        border: round $primary; background: $surface;
    }
    SelectList #picker-title {
        padding: 0 1; text-style: bold; background: $primary; color: $background;
    }
    /* height: an Input is three rows by default, two of them blank here */
    SelectList Input, SelectList Input:focus { height: 1; border: none; padding: 0 1; background: $surface; }
    SelectList OptionList { height: auto; max-height: 20; border: none; background: $surface; }
    SelectList OptionList > .option-list--option-highlighted {
        background: $primary 30%; text-style: bold;
    }
    """

    def __init__(
        self, items: list[Item], *, title: str, current: Any = None, preview: Callable[[Any], None] | None = None
    ) -> None:
        """Set the picker up.

        Args:
            items: The rows, in order.
            title: The heading.
            current: The value to start on, shown bold; `None` starts on the first row.
            preview: Called with every value the highlight lands on, or `None`.
        """
        super().__init__()
        self.items = items
        self.title_text = title
        self.current = current
        self.preview = preview
        """Called with every value the highlight lands on: the theme picker shows
        the theme you are on before you pick it."""
        self.shown: list[Item] = list(items)
        self._cursor_at = 0

    def compose(self) -> ComposeResult:
        """Build the title, the filter box and the option list.

        Yields:
            The widgets, top to bottom.
        """
        with Vertical():
            yield Static(literal(self.title_text), id="picker-title")
            yield Input(placeholder="filter")
            yield OptionList()

    def on_mount(self) -> None:
        """Fill the list on the current value and give the filter box the keyboard."""
        # straight to the current value: highlighting row 0 first would preview it
        self._populate(next((index for index, item in enumerate(self.shown) if item.value == self.current), 0))
        options = self.query_one(OptionList)
        # after the refresh: at mount time the list has no geometry yet, so the
        # scroll lands nowhere and a picker opened on a far-down current value
        # (21 themes, one library) shows the top of the list instead
        self.call_after_refresh(options.scroll_to_highlight)
        self.query_one(Input).focus()

    def _prompt(self, item: Item, *, here: bool) -> str:
        """Render one row.

        The cursor points at the row you are on: every picker has one of those, while only some are opened
        with a `current` value. That one is marked by being bold, not by the cursor, or the two would fight
        for the same cell.

        Args:
            item: The row.
            here: The highlight is on this row.

        Returns:
            The row as Textual markup.
        """
        # `literal`: labels are file names, titles and refs, and `[weird].pdf`
        # is text, not a tag
        label = literal(item.label)
        label = f"[bold]{label}[/]" if item.value == self.current else label
        hint = f"  [dim]{literal(item.hint)}[/]" if item.hint else ""
        return f"{glyph('cursor') if here else ' '} {label}{hint}"

    def _populate(self, highlight: int = 0) -> None:
        """Rebuild the option list from the rows that pass the filter.

        Args:
            highlight: The index to highlight; ignored when no row is left.
        """
        options = self.query_one(OptionList)
        options.clear_options()
        for index, item in enumerate(self.shown):
            options.add_option(self._prompt(item, here=index == highlight))
        options.highlighted = highlight if self.shown else None

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Move the cursor glyph with the highlight — two rows change, not the list.

        Args:
            event: The highlight's event, naming the new index.
        """
        options = self.query_one(OptionList)
        for index in {self._cursor_at, event.option_index}:
            if 0 <= index < len(self.shown):
                options.replace_option_prompt_at_index(
                    index, self._prompt(self.shown[index], here=index == event.option_index)
                )
        self._cursor_at = event.option_index
        if self.preview is not None and 0 <= event.option_index < len(self.shown):
            self.preview(self.shown[event.option_index].value)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the rows whenever the filter box changes.

        Args:
            event: The change event, holding the new text.
        """
        needle = event.value.casefold()
        self.shown = [item for item in self.items if item.matches(needle)]
        self._populate()

    def on_key(self, event: Any) -> None:
        """Dispatch through `[modes.picker]` — the same table as everything else.

        Args:
            event: The key event; stopped when the key is bound.
        """
        token = keymap.normalize(event.key, event.character)
        binding = self.app.keymap.lookup("picker", (token,))
        if binding is None:
            return  # anything else is filter text
        event.stop()

        options = self.query_one(OptionList)
        if binding.command in ("nav.down", "nav.up"):
            step = binding.args.get("count", 1) * (1 if binding.command == "nav.down" else -1)
            if options.option_count:
                options.highlighted = max(0, min(options.option_count - 1, (options.highlighted or 0) + step))
        elif binding.command == "picker.confirm":
            index = options.highlighted
            picked = self.shown[index] if index is not None and self.shown else None
            self.dismiss((picked.value, binding.args.get("invert", False)) if picked else None)
        elif binding.command == "picker.cancel":
            self.dismiss(None)


def guarded(what: str, function: Callable[..., None]) -> Callable[..., None]:
    """Wrap a function as a screen callback that cannot end the session.

    An exception out of a `push_screen` callback ends a Textual app, so it is logged once, as in
    `PtuiApp.run_command`: the message in the pane, the traceback in log.file.

    Args:
        what: What the callback does, for the log.
        function: The callback.

    Returns:
        A function taking the same positional arguments and returning nothing.
    """

    def call(*args: Any) -> None:
        """Run the callback, logging any exception.

        Args:
            *args: The callback's arguments.
        """
        try:
            function(*args)
        except Exception as error:  # a broken answer must not kill the session
            logger.exception(f"{what} failed: {error}")

    return call


def pick(
    app: Any, items: list[Item], *, title: str, current: Any = None, preview: Callable[[Any], None] | None = None
) -> Callable[..., Any]:
    """`app.push_screen(SelectList(...), on_confirm)`, minus the ceremony.

    Usage: `ui.pick(app, items, title="Sort by")(lambda value, inverted: ...)`.
    A `preview` is called with each highlighted value, and with `current` again
    when the picker is cancelled — back to what was there when it opened.

    Args:
        app: The running `PtuiApp`.
        items: The rows, in order.
        title: The heading.
        current: The value to start on, or `None`.
        preview: Called with each highlighted value, or `None`.

    Returns:
        A function that takes the answer callback, called as `(value, inverted)`, and opens the picker.
    """
    if preview is not None:  # it runs on every highlight, outside any other guard
        preview = guarded(f"{title} preview", preview)

    def run(on_confirm: Callable[[Any, bool], None]) -> None:
        """Open the picker.

        Args:
            on_confirm: Called with `(value, inverted)` when something is picked.
        """

        def handle(result: tuple[Any, bool] | None) -> None:
            """Pass the answer on, or put the previewed value back on a cancel.

            Args:
                result: `(value, inverted)`, or `None` when cancelled.
            """
            if result is not None:
                on_confirm(*result)
            elif preview is not None:
                preview(current)

        app.push_screen(SelectList(items, title=title, current=current, preview=preview), guarded(title, handle))

    return run


@dataclass(frozen=True, slots=True)
class FileChoice:
    """One `files` entry in the delete dialog, with why it is (un)checked.

    Attributes:
        label: The entry as shown.
        checked: Whether the dialog starts with it ticked.
        note: Why it is or is not ticked; empty for no note.
    """

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
        """Set the dialog up.

        Args:
            summary: One line per document going.
            files: The files that may go with them.
            title: The heading.
        """
        super().__init__()
        self.summary = summary
        self.files = files
        self.title_text = title

    def compose(self) -> ComposeResult:
        """Build the title, the document list, the file checkboxes and the key legend.

        Yields:
            The widgets, top to bottom.
        """
        with Vertical():
            yield Static(self.title_text, id="picker-title")
            yield Static("\n".join(self.summary), id="delete-docs")
            if self.files:
                yield SelectionList[int](
                    *(
                        (
                            f"{literal(choice.label)}{f'  [dim]{literal(choice.note)}[/]' if choice.note else ''}",
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
        """Give the file checkboxes the keyboard, when there are any."""
        if self.files:
            self.query_one(SelectionList).focus()

    def action_confirm(self) -> None:
        """Dismiss with the indices of the ticked files."""
        self.dismiss(list(self.query_one(SelectionList).selected) if self.files else [])

    def action_cancel(self) -> None:
        """Dismiss with `None`."""
        self.dismiss(None)


# ponytail: a fixed field list for adding; editing reads `edit.structured_fields`.
ADD_FIELDS = ("title", "author", "year", "ref", "tags", "doi")

FIELD_LABELS = {"author_list": "author"}
"""`author_list` is edited as papis's one-line `author` string."""


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
    AddForm #form-keys { padding: 1 2 0 2; color: $text-muted; }
    """

    def __init__(
        self,
        source: Path | None,
        data: dict[str, str],
        preview: Callable[[dict], str] | None,
        *,
        fields: tuple[str, ...] = ADD_FIELDS,
        title: str = "",
    ) -> None:
        """Set the form up.

        Args:
            source: The file being added, or `None` for a metadata-only add.
            data: Field name to its starting text.
            preview: Turns the form's values into the destination preview, or `None` for no preview.
            fields: The fields to show, in order.
            title: The heading; empty means `Add <what>`.
        """
        super().__init__()
        self.source = source
        """None for a metadata-only add — an importer that found no PDF."""
        self.data = data
        self.preview = preview
        self.fields = fields
        self.title_text = title

    def compose(self) -> ComposeResult:
        """Build the title, one labelled input per field, the preview and the key legend.

        Yields:
            The widgets, top to bottom.
        """
        with Vertical():
            what = self.source.name if self.source else "from metadata"
            # `literal`: a file name may hold brackets, and this is the one
            # string here that goes through markup.
            yield Static(literal(self.title_text or f"Add {what}"), id="picker-title")
            for field_name in self.fields:
                yield Static(FIELD_LABELS.get(field_name, field_name), classes="field-label")
                yield Input(value=str(self.data.get(field_name, "")), id=f"add-{field_name}")
            # markup=False: a destination path is full of `[doc[year]]`-shaped
            # text that Textual would otherwise read as markup.
            yield Static(id="add-preview", markup=False)
            # Textual's own keys, not the keymap's: a modal owns the keyboard
            yield Static(
                "[$accent]tab[/]/[$accent]shift+tab[/] move   [$accent]enter[/] save   [$accent]escape[/] cancel",
                id="form-keys",
            )

    def on_mount(self) -> None:
        """Show the first preview and focus the first field."""
        self._refresh_preview()
        self.query(Input).first().focus()

    def _values(self) -> dict[str, str]:
        """Read the form.

        Returns:
            Field name to its stripped text, for the fields that hold any.
        """
        return {
            name: self.query_one(f"#add-{name}", Input).value.strip()
            for name in self.fields
            if self.query_one(f"#add-{name}", Input).value.strip()
        }

    def _refresh_preview(self) -> None:
        """Redraw the destination preview, when the form has one."""
        if self.preview is not None:
            self.query_one("#add-preview", Static).update(self.preview(self._values()))

    def on_input_changed(self, _: Input.Changed) -> None:
        """Redraw the preview whenever a field changes.

        Args:
            _: Unused; Textual passes it.
        """
        self._refresh_preview()

    def on_input_submitted(self, _: Input.Submitted) -> None:
        """Dismiss with the form's values when `enter` is pressed in a field.

        Args:
            _: Unused; Textual passes it.
        """
        self.dismiss(self._values())

    def on_key(self, event: Any) -> None:
        """Dismiss with `None` on `escape`.

        Args:
            event: The key event; stopped when it is `escape`.
        """
        if event.key == "escape":
            self.dismiss(None)
            event.stop()


class EditForm(AddForm):
    """Show the add form over a document's own fields: `doc.edit` in `structured` mode.

    Every field comes back, empty ones included: emptying a field is how it is removed.
    """

    DEFAULT_CSS = "EditForm #add-preview { display: none; }  /* nothing is placed */"

    def __init__(self, title: str, fields: tuple[str, ...], data: dict[str, str]) -> None:
        """Set the form up.

        Args:
            title: The heading.
            fields: The fields to show, in order.
            data: Field name to its starting text.
        """
        super().__init__(None, data, None, fields=fields, title=title)

    def _values(self) -> dict[str, str]:
        """Read the form.

        Returns:
            Field name to its stripped text, for every field.
        """
        return {name: self.query_one(f"#add-{name}", Input).value.strip() for name in self.fields}
