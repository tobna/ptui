"""The Textual app: panes, key dispatch, and the state every command mutates.

Key handling deliberately bypasses Textual's own bindings. Every keypress goes
through `keymap` -> `commands.REGISTRY`, so keys.toml is the only place bindings
live and the help, hint bar and which-key panel all derive from the same table.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from fnmatch import fnmatch
from typing import Any

import papis.config
import papis.format
from loguru import logger
from papis.document import Document
from rich.cells import cell_len
from rich.markup import escape
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.content import Content
from textual.coordinate import Coordinate
from textual.theme import Theme
from textual.widgets import DataTable, Input, OptionList, RichLog, Static

from ptui import (
    actions,
    commands,
    config,
    doctor,
    keymap,
    library,
    place,
    ui,
    undo,
)

PANES = keymap.PANES

DEFAULT_THEME = "tokyonight-moon"
"""LazyVim's own default colourscheme. `\\ t` switches, `[ui] theme` makes it stick."""

EXTRA_THEMES = (
    # Values straight out of tokyonight.nvim's `colors/moon.lua`, not matched by
    # eye. Textual ships `tokyo-night` already, but that is the *night* variant;
    # LazyVim defaults to `style = "moon"`, which is a different palette.
    Theme(
        name="tokyonight-moon",
        background="#1e2030",  # bg_dark — the ground the panes sit on
        surface="#222436",  # bg — the editor background proper
        panel="#2f334d",  # bg_highlight — status blocks, which-key
        foreground="#c8d3f5",
        primary="#82aaff",  # blue: borders, titles, the cursor row
        secondary="#c099ff",  # magenta
        accent="#ff966c",  # orange: marks and the hint-bar keys
        warning="#ffc777",
        error="#ff757f",
        success="#c3e88d",
        boost="#3b4261",
        dark=True,
        variables={"text-muted": "#828bb8", "border-blurred": "#3b4261"},
    ),
)
"""Themes ptui adds to Textual's own. One entry is not a plugin system."""

STATUS_BG = "$surface"
"""The status bar's own background — what the powerline blocks flow out of."""

READING_COLOURS = {"unread": "dim", "reading": "$warning", "read": "$success"}
"""`reading_status` is a state, so it gets a colour rather than a word to read.
An unknown value falls back to dim — the field tolerates free strings."""

LOG_TAGS = {"[red]": "error", "[yellow]": "warning", "[green]": "success"}
"""What a call site's `[red]` means, in theme terms. The log pane is a `RichLog`
and parses *Rich* markup, where `$error` is meaningless — so the translation
happens in `log_line`, once, instead of at every call site."""

LOG_MARKUP = ("[red]", "[yellow]", "[green]", "[dim]", "[bold]", "[/]")
"""The only markup a `log_line` caller may write. Anything else in the message
is a value and is printed as it is."""

LOG_LEVELS = {
    "CRITICAL": "error",
    "ERROR": "error",
    "WARNING": "warning",
    "SUCCESS": "success",
    "DEBUG": "text-muted",
    "TRACE": "text-muted",
}
"""Loguru level -> the theme variable that colours it in the log pane. INFO is
absent on purpose: the ordinary line is the one that must not shout."""

MARKER_WIDTH = 2
"""Cells the flexible column gives up to the exception marker and its space.

One cell, always reserved, so the titles stay aligned whether or not a document
has anything wrong with it. It is the *only* exception marker: a missing file is
already a doctor finding (papis's `files` check), and how many files a document
has is information rather than an exception — that belongs in the info pane.
"""

MIN_FLEX = 12
"""Cells the flexible column keeps. Below this a fixed column is dropped instead —
author plus a stubby title beats a wide title and no idea who wrote it."""

INFO_FIELDS = (
    "author",
    "year",
    "venue",
    "ref",
    "doi",
    "url",
    "tags",
    "notes",
    "reading_status",
    "rating",
)
"""Info-pane fields, in order. Each needs a `field.<name>` entry in `ui.GLYPHS`."""


class PapisToLoguru(logging.Handler):
    """papis's stdlib records, re-emitted through loguru and so into the log
    pane and `log.file`, tagged so the pane can keep papis's INFO out."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        message = f"{record.name}: {record.getMessage()}"
        logger.bind(papis=True).opt(exception=record.exc_info).log(level, message)


class ListTable(DataTable):
    """The document list. Refits its columns whenever its own size changes.

    The app-level resize handler is too early — widget geometry is only real
    once layout has run, and that is exactly when this fires.
    """

    def on_resize(self, _: events.Resize) -> None:
        self.app.refresh_rows()  # type: ignore[attr-defined]
        self.app.relayout()  # type: ignore[attr-defined]  # now the widths are real


class LogPane(RichLog):
    """The operation log, wrapped at a width it works out itself.

    `RichLog` wraps a line once, at its own width when the line is written —
    zero while the pane is hidden, which it mostly is, so those lines came out
    one cell wide; and a terminal narrowed later left every older line running
    under the scrollbar. The pane spans the screen, so its width is the
    screen's less the border and the scrollbar (`overflow-y: scroll` keeps that
    one on screen), and `rewrap` lays every kept line out again on a resize.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.entries: deque[str] = deque(maxlen=self.max_lines)

    def wrap_width(self, screen_width: int) -> int:
        return max(1, screen_width - self.styles.gutter.width - self.styles.scrollbar_size_vertical)

    def add(self, line: str) -> None:
        self.entries.append(line)
        self.write(line, width=self.wrap_width(self.app.size.width))

    def rewrap(self, screen_width: int) -> None:
        width = self.wrap_width(screen_width)
        if width == self.virtual_size.width:  # every row is padded to the wrap width
            return  # ~70 ms for a full log, and a window drag sends many resizes
        self.clear()
        for line in self.entries:
            self.write(line, width=width)


class PtuiApp(App[None]):
    """Layout CSS lives here; themes only recolour it."""

    ENABLE_COMMAND_PALETTE = False
    """Textual's own palette runs as a system binding, above `on_key`, and would
    keep `ctrl+p` from ever reaching `cmdline.open`. Off now that ours exists."""

    CSS = """
    Screen { layers: base which; background: $background; }
    #panes { height: 1fr; }

    #list-pane, #info-pane, #files-pane, #log-pane {
        background: $surface; border: round $border-blurred;
        border-title-color: $text-muted; border-title-style: bold;
        border-subtitle-color: $text-muted;
        /* One cell instead of Textual's two. `fit_columns` reads the real
           `scrollbar_size_vertical`, so the freed cell goes to the title. */
        scrollbar-size-vertical: 1;
    }
    #list-pane { width: 1fr; }
    #side { width: 1fr; height: 1fr; }
    #info-pane { width: 1fr; height: 1fr; padding: 0 1; }
    /* transient, like the log: it is where you act on files, and the info pane
       already lists them for reading */
    #files-pane { height: auto; max-height: 12; display: none; }
    #files-pane > .option-list--option-highlighted { background: $primary 30%; text-style: bold; }
    #log-pane { height: 10; display: none; }
    /* ID beats class in Textual's specificity, so the active rule has to carry
       the ID too — otherwise the pane border never lights up. */
    #list-pane.pane-active, #info-pane.pane-active, #files-pane.pane-active, #log-pane.pane-active {
        border: round $primary; border-title-color: $primary;
    }

    DataTable > .datatable--header { background: $surface; color: $text-muted; text-style: bold; }
    DataTable > .datatable--cursor { background: $primary 30%; text-style: bold; }
    DataTable > .datatable--hover { background: $boost; }

    #status-bar { height: 1; background: $surface; }
    #hint-bar { height: 1; background: $background; color: $text-muted; }
    #prompt {
        display: none; height: 1; border: none; padding: 0 1;
        background: $panel; color: $text;
    }
    #prompt.open { display: block; }
    #which-key {
        layer: which; display: none; dock: bottom; height: auto; width: auto;
        offset: 2 -3; padding: 0 1; background: $surface; border: round $primary;
        border-title-color: $primary; border-title-style: bold;
    }
    #which-key.open { display: block; }
    """

    def __init__(self, cfg: config.Config, km: keymap.Keymap) -> None:
        super().__init__()
        self.cfg = cfg
        self.km = km
        ui.use_icons(cfg.get("ui.icons", False))  # every glyph goes through ui.glyph
        self.mode = "list"
        self.pending: keymap.Chord = ()
        self.docs: list[Document] = []  # the scoped set
        self.rows: list[Document] = []  # the scoped set after narrowing
        self.marks: set[str] = set()
        self.visual: tuple[str, bool, set[str]] | None = None
        """`v`/`V`: (anchor `papis_id`, unmarking, the marks before it started)."""
        self.scope_query = cfg.get("general.startup_query", "")
        self.narrow_query = ""
        self.marked_only = False
        self.doctor_only = False
        """The doctor view: narrow to documents with findings, as the names of
        the checks that ran (`False` when off). Same shape as `marked_only` — a
        filter over the scoped set, not a separate widget."""
        self.sort_key, self.sort_reverse = self._default_sort()
        self.prompt_kind = ""
        layout = cfg.get("ui.layout", "auto")
        self.layout_auto = layout == "auto"
        """Cleared by `z z`: an explicit choice outlives any resize."""
        self.side_by_side = layout != "horizontal"  # `auto` re-decides in choose_layout
        self.split = cfg.get("ui.split_ratio", 0.6)
        self.history = undo.History(cfg.get("undo.stack_size", 50))
        """One operation is one step, not one document — see `undo.py`."""
        self._fit: list[tuple[dict[str, Any], int]] = []
        self._cells: dict[str, tuple[int, Document, dict[tuple[str, str | None], str]]] = {}
        """`cell_text`'s cache: `papis_id` -> (mtime, flattened document, texts)."""
        self._paint: tuple[list[str], int, str, str] = ([], 1, "", "")
        """What `refresh_rows` paints with: highlighted terms, row height, styles."""
        self._fit_state: tuple[list[tuple[dict[str, Any], int]], list[str]] | None = None
        self._which_timer: Any = None
        self.file_index = 0
        """The files pane's cursor: an index into the current document's `files`."""
        self._files_of: str | None = None
        self._info_of: str | None = None
        self._recall, self._draft = 0, ""
        """The prompt's place in the query history (0: the line being typed) and that line."""

    # ── setup ───────────────────────────────────────────────────────────────

    def apply_theme(self, name: str) -> None:
        """Switch palette. Textual owns most of the themes — 21 of them, light and dark,
        each a full set of variables the CSS above already speaks. An unknown
        name says so and keeps the default rather than starting unreadable."""
        for theme in EXTRA_THEMES:
            if theme.name not in self.available_themes:
                self.register_theme(theme)
        if name and name not in self.available_themes:
            self.log_line(f"[yellow]no theme {name!r}; try `\\ t`[/]")
            return
        self.theme = name or DEFAULT_THEME

    def _default_sort(self) -> tuple[str, bool]:
        presets = self.cfg.get("list.sort_presets", [])
        preset = next((p for p in presets if p.get("default")), presets[0] if presets else {})
        return preset.get("key", "time-added"), preset.get("dir", "desc") == "desc"

    def compose(self) -> ComposeResult:
        table = ListTable(id="list-pane", cursor_type="row", zebra_stripes=False)
        table.can_focus = False  # all keys go through our dispatcher
        table.border_title = "library"
        info = VerticalScroll(Static(id="info"), id="info-pane")
        info.can_focus = False
        info.border_title = "document"
        files = OptionList(id="files-pane")
        files.can_focus = False
        files.border_title = "files"
        with Container(id="panes"):  # apply_split() owns the layout direction
            yield table
            with Vertical(id="side"):
                yield info
                yield files
        # wrap: a finding quotes a whole path, and the pane has no horizontal scroll key
        log = LogPane(id="log-pane", markup=True, wrap=True, max_lines=self.cfg.get("log.max_entries", 500))
        log.border_title = "log"
        yield log
        yield Static(id="which-key")
        yield Static(id="hint-bar")
        yield Static(id="status-bar")
        yield Input(id="prompt")

    def on_mount(self) -> None:
        self.apply_theme(self.cfg.get("ui.theme", DEFAULT_THEME))
        self.apply_split()
        self._setup_logging()
        for problem in self.km.unknown_commands:
            self.log_line(f"[yellow]keys.toml:[/] {problem}")
        for problem in self.cfg.invalid:
            self.log_line(f"[yellow]config.toml:[/] {problem}")
        if editor := self.cfg.get("edit.editor"):  # overrides papis's `editor`, which papis's edit reads
            papis.config.set("editor", editor)
        if name := self.cfg.get("general.library"):  # `-l` or [general] library
            try:
                library.use(name)
            except Exception as exc:
                self.log_line(f"[red]library {name!r}:[/] {exc}")
                self.cfg.data["general"]["library"] = ""
        cursor = actions.restore_state(self)
        actions.reload(self)
        if cursor:
            self.refresh_rows(keep=cursor)
        # After the library is loaded, not before: column widths come from the
        # p90 of real values, and an empty list makes every column look narrower
        # than it is — which decided the layout wrongly on a borderline width.
        self.relayout()
        self.focus_pane("list")
        actions.warn_untracked(self)  # once per session, and only under git
        if self.cfg.get("doctor.scan_on_startup", True):
            self.scan_library()

    def on_unmount(self) -> None:
        # The widgets are gone by now, so not `self.current`: the document the
        # info pane last showed is the one the cursor was on.
        actions.save_state(self, self._info_of)

    def apply_config(self) -> None:
        """Re-apply what is read once instead of at every use — after `config.set`.

        Everything else (columns, sort tiebreak, narrow mode, …) is read when it
        is used, so the redraw at the end is all it needs.
        """
        ui.use_icons(self.cfg.get("ui.icons", False))
        self.apply_theme(self.cfg.get("ui.theme", DEFAULT_THEME))
        if editor := self.cfg.get("edit.editor"):
            papis.config.set("editor", editor)
        layout = self.cfg.get("ui.layout", "auto")
        self.layout_auto = layout == "auto"
        if not self.layout_auto:
            self.side_by_side = layout != "horizontal"
        self.split = self.cfg.get("ui.split_ratio", 0.6)
        self._cells.clear()  # icons, strip_latex and the columns all shape a cell
        self._fit_state = None
        self.choose_layout()
        self.apply_split()
        self.apply_sort()
        self.refilter()

    def relayout(self) -> None:
        """Re-decide the `auto` layout and apply it only if it changed. Applying
        unconditionally would resize, be told about the resize, and resize again."""
        was = self.side_by_side
        self.choose_layout()
        if was != self.side_by_side:
            self.apply_split()

    def _setup_logging(self) -> None:
        logger.remove()
        pane = self.query_one(LogPane)
        level = self.cfg.get("log.level", "info").upper()

        def write(message: Any) -> None:
            """One loguru record, coloured by its level.

            `RichLog` parses *Rich* markup, where `$error` means nothing, so the
            colour is looked up in `theme_variables` — the hex the CSS would
            have used. A record's text is escaped: a warning quotes the offending
            value, and papis is full of `[...]`-shaped ones.
            """
            record = message.record
            body = escape(record["message"])
            colour = self.theme_variables.get(LOG_LEVELS.get(record["level"].name, ""), "")
            line = f"[dim]{record['time']:%H:%M:%S}[/] " + (f"[{colour}]{body}[/]" if colour else body)
            # papis logs from the importer and download threads too, and a widget
            # may only be touched from the app's own
            if self._thread_id == threading.get_ident():
                pane.add(line)
            else:
                self.call_from_thread(pane.add, line)

        # papis's INFO chatter goes to the file only; its warnings are the
        # "see the papis log" that attach and add point at.
        logger.add(
            write,
            level=level,
            format="{message}",
            filter=lambda record: "papis" not in record["extra"] or record["level"].no >= logging.WARNING,
        )
        log_file = self.cfg.as_path("log.file")
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            logger.add(log_file, level=level, rotation="1 MB", retention=3)

        # papis logs through the stdlib. Under `papis ptui` its handler holds the
        # terminal's stderr from before Textual started and would print over the
        # screen; standalone, its records went nowhere. Both now land here.
        papis_log = logging.getLogger("papis")
        for handler in list(papis_log.handlers):
            papis_log.removeHandler(handler)
        papis_log.addHandler(PapisToLoguru())
        papis_log.setLevel(level)
        papis_log.propagate = False

    # ── key dispatch ────────────────────────────────────────────────────────

    async def on_key(self, event: Any) -> None:
        if len(self.screen_stack) > 1:
            return  # a modal owns the keyboard
        if self.prompt_kind:
            if event.key == "escape":
                self.close_prompt()
                event.stop()
            elif event.key in ("up", "down") and self.prompt_kind in actions.HISTORY_KINDS:
                self.recall(1 if event.key == "up" else -1)
                event.stop()
            return

        event.stop()
        event.prevent_default()
        self.hide_which_key()
        key = keymap.normalize(event.key, event.character)
        # Escape is guaranteed above the keymap: a mode may define few keys, but
        # none may trap the keyboard. In list mode it keeps its escape_chain meaning.
        if key == "escape" and self.mode != "list":
            self.pending = ()
            self.focus_pane("list")
            return
        # Help is guaranteed the same way, and follows its [modes.list] binding:
        # rebind help.show there and `?` moves in every mode at once.
        help_key = self.km.for_command("list", "help.show")
        if not self.pending and key == help_key and self.km.lookup(self.mode, (key,)) is None:
            self.run_command("help.show")
            return
        chord = (*self.pending, key)
        binding = self.km.lookup(self.mode, chord)
        if binding is not None:
            self.pending = ()
            self.run_command(binding.cmd, binding.args)
        elif self.km.is_prefix(self.mode, chord):
            self.pending = chord
            self.schedule_which_key()
        else:
            if self.pending:
                self.log_line(f"[dim]no binding for {' '.join(chord)}[/]")
            self.pending = ()
        self.refresh_status()

    def run_command(self, name: str, args: dict[str, Any] | None = None) -> None:
        if name not in commands.REGISTRY:  # asked first: a KeyError from inside a command is a failure
            self.log_line(f"[yellow]{name} is not implemented yet[/]")
            return
        try:
            commands.run(name, self, args)
        except Exception as exc:  # a broken command must not kill the session
            # once: the loguru sink puts it in the pane, the traceback goes to log.file
            logger.exception(f"{name} failed: {exc}")

    # ── which-key ───────────────────────────────────────────────────────────

    def schedule_which_key(self) -> None:
        if not self.km.option("which_key", True):
            return
        delay = self.km.option("which_key_delay_ms", 400) / 1000
        self._which_timer = self.set_timer(delay, self.show_which_key)

    def show_which_key(self) -> None:
        rows = self.km.under_prefix(self.mode, self.pending)
        if not rows:
            return
        panel = self.query_one("#which-key", Static)
        panel.border_title = " ".join(self.pending)
        panel.update(
            "\n".join(
                f"[$accent bold]{ui.literal(b.keys[len(' '.join(self.pending)) + 1 :])}[/]  {ui.literal(b.desc or b.cmd)}"
                for b in rows
            )
        )
        panel.add_class("open")

    def hide_which_key(self) -> None:
        if self._which_timer is not None:
            self._which_timer.stop()
            self._which_timer = None
        self.query_one("#which-key", Static).remove_class("open")

    # ── panes ───────────────────────────────────────────────────────────────

    def on_resize(self, event: events.Resize) -> None:
        """Re-decide the `auto` layout, from the *event's* width: `App.size` still
        holds the old one here, which is the same "fires before layout" problem
        that put the column refit on `ListTable.on_resize` instead. That handler
        calls `relayout()` afterwards, once the scrollbar and widget widths are
        real, which is what settles a borderline width.
        """
        was = self.side_by_side
        self.choose_layout(event.size.width)
        if was != self.side_by_side:
            self.apply_split()
        self.query_one(LogPane).rewrap(event.size.width)

    def flex_width_if_side_by_side(self, total: int | None = None) -> int:
        """What the flexible column would get with the info pane beside the list.

        Asked before anything is resized, which is the only way to break the
        circularity: the layout depends on the column fit, which depends on the
        pane width, which depends on the layout.
        """
        total = self.size.width if total is None else total
        fit = self.fit_columns(int(total * self.split))
        title = self.title_column
        return next((width for column, width in fit if column is title), 0)

    def choose_layout(self, total: int | None = None) -> None:
        """`ui.layout = "auto"`: side by side only while the flexible column still
        reaches `list.flex_target`. Expressed as a column target rather than a
        terminal width so that adding a column moves the threshold by itself.

        A manual `z z` ends automatic choice for the session — once the user has
        said which layout they want, a resize must not argue.
        """
        if not self.layout_auto:
            return
        target = self.cfg.get("list.flex_target", 45)
        self.side_by_side = self.flex_width_if_side_by_side(total) >= target

    def apply_split(self) -> None:
        """Size the panes for the current layout. The only place that sets either.

        Both dimensions are set every time: a leftover width from the previous
        layout is exactly why `z z` used to do nothing visible. The list takes
        `split` of the axis, or the whole window when it is the only pane left.
        """
        # Deliberately does not call choose_layout: `App.size` is stale during a
        # resize, so the decision is made once by the caller that knows the real
        # width, and applying it must not second-guess that with a worse number.
        self.query_one("#panes").styles.layout = "horizontal" if self.side_by_side else "vertical"
        table = self.query_one(DataTable)
        side = self.query_one("#side")
        info = self.query_one("#info-pane").display
        side.display = info or self.query_one("#files-pane").display
        # Stacked, the files pane alone (`z i`) is as tall as its entries and the
        # list has the rest: the side's share of the height left blank rows under it.
        fit = side.display and not info and not self.side_by_side
        share = f"{int(self.split * 100)}%" if side.display and not fit else "1fr"
        table.styles.width, table.styles.height = (share, "1fr") if self.side_by_side else ("1fr", share)
        side.styles.width = "1fr"
        side.styles.height = "auto" if fit else "1fr"
        self.call_after_refresh(self.refresh_rows)  # columns refit to the new width

    # ── columns ─────────────────────────────────────────────────────────────

    def cell_text(self, doc: Document, column: dict[str, Any]) -> str:
        """What a column renders for a document, before it is cut to width.

        A column with `glyph = "type"` renders its value through the `type.*`
        family instead of printing it, so `inproceedings` becomes one cell.

        Cached per document, stamped with `info.yaml`'s mtime: formatting is most
        of a redraw (papis reads its config on every call), and every narrow
        keystroke, mark and sort redraws every row — 3 s at 5000 documents.
        A document written since it was drawn is re-read before it is drawn
        again: formatting the copy in memory cached the old text under the new
        stamp, and the row kept it even after a rescan.
        """
        key, stamp = library.doc_id(doc), library.mtime(doc)  # stat, then read
        hit = self._cells.get(key)
        if hit is None or hit[0] != stamp:
            if hit is not None:
                doc.load()
            hit = self._cells[key] = (stamp, library.flatten(doc), {})
        texts = hit[2]
        slot = (column["format"], column.get("glyph"))
        if slot not in texts:
            texts[slot] = self._render(hit[1], column)
        return texts[slot]

    def _render(self, flat: Document, column: dict[str, Any]) -> str:
        try:
            text = papis.format.format(column["format"], flat)
        except papis.format.FormatFailedError:
            # `{doc[author_list][0][family]}` on a document with no authors. Not
            # `default=""`: papis then logs a warning with a traceback, and builds
            # the document's description for it, on every cell of every redraw.
            text = ""
        if family := column.get("glyph"):
            return ui.glyph(f"{family}.{text.strip().casefold()}", f"{family}.misc")
        return library.strip_latex(text) if self.cfg.get("list.strip_latex", True) else text

    @property
    def sort_format(self) -> str:
        """The sort key written the way a column writes it:
        `author_list.0.family` -> `{doc[author_list][0][family]}`. That makes
        "is the list sorted by this column?" a string comparison, and a sort key
        no column shows (`time-added`) simply matches nothing."""
        return "{doc[" + "][".join(self.sort_key.split(".")) + "]}"

    @property
    def title_column(self) -> dict[str, Any] | None:
        """The first `width = 0` column: the one that wraps and carries the
        exception marker. Any further flexible column only shares the room."""
        return next((column for column in self.cfg.get("list.columns", []) if not column["width"]), None)

    def header(self, column: dict[str, Any], first: bool = False) -> str:
        """`Year ↓` when the list is sorted on that column's own field.

        Indented past whatever leads the cells under it — the exception marker
        in the title column, the mark glyph in the first one — or the header
        sits left of every value it names.
        """
        pad = " " * MARKER_WIDTH if column is self.title_column else ""
        if first:
            pad = "  " + pad
        if column["format"].strip() != self.sort_format:
            return pad + column["title"]
        arrow = ui.glyph("sort_desc" if self.sort_reverse else "sort_asc")
        return f"{pad}{column['title']} {arrow}"

    def natural_width(self, index: int, column: dict[str, Any]) -> int:
        """What a fixed column asks for: its p90 over the whole narrowed set,
        capped by the configured width and never narrower than its header.

        Computed over `app.rows`, not the visible window — sizing to what is on
        screen makes the column jitter while scrolling.
        """
        wanted = library.p90([self.cell_text(doc, column) for doc in self.rows])
        if index == 0:
            wanted += 2  # the mark glyph and its space
        return min(column["width"], max(wanted, cell_len(self.header(column, index == 0))))

    def fit_columns(self, pane_width: int | None = None) -> list[tuple[dict[str, Any], int]]:
        """The columns that fit the pane, with the width each one gets.

        Configured widths are a ceiling, not a reservation: a column takes the
        p90 of what it actually holds. The `width = 0` column absorbs whatever
        is left. A fixed column that would starve it is dropped instead — that
        is how `Tags` disappears on a narrow terminal and comes back on a wide
        one, rather than the list scrolling sideways.

        A column marked `optional = true` is allocated only after every required
        one, and only while the flex column stays at `list.flex_target`. Without
        that, `Tags` could survive on a 20-cell `Title` — the wrong trade, since
        the required columns are the ones that identify a document.

        `pane_width` asks the hypothetical question the `auto` layout needs —
        "what would the flex column get in a pane this wide?" — without
        resizing anything.
        """
        table = self.query_one(DataTable)
        spec = self.cfg.get("list.columns", [])
        pad = table.cell_padding * 2
        width = table.size.width if pane_width is None else pane_width
        room = width - 2 - table.scrollbar_size_vertical  # border, scrollbar
        flexes = [i for i, column in enumerate(spec) if not column["width"]]
        widths: dict[int, int] = {}
        # Required columns first, so an optional one can never outbid them. Each
        # pass keeps the flex columns above their floor: bare survival for the
        # required ones, comfort (for the title column) for the optional ones.
        for optional in (False, True):
            reserve = self.cfg.get("list.flex_target", 45) if optional else MIN_FLEX
            floor = reserve + pad + (len(flexes) - 1) * (MIN_FLEX + pad) if flexes else 0
            for index, column in enumerate(spec):
                if index in flexes or bool(column.get("optional")) is not optional:
                    continue
                want = self.natural_width(index, column)
                if room - want - pad < floor:
                    continue
                room -= want + pad
                widths[index] = want
        # Several `width = 0` columns split what is left; the first gets the odd cells.
        share, extra = divmod(room - pad * len(flexes), len(flexes) or 1)
        for n, index in enumerate(flexes):
            widths[index] = max(MIN_FLEX, share + (extra if n == 0 else 0))
        return [(spec[index], widths[index]) for index in sorted(widths)]

    def sync_columns(self) -> None:
        """Rebuild the table's columns when the fit or the headers changed — a
        resize, `z z`, or a new sort key that moves the direction arrow."""
        fit = self.fit_columns()
        headers = [self.header(column, index == 0) for index, (column, _) in enumerate(fit)]
        if (fit, headers) == self._fit_state:
            return
        self._fit_state = (fit, headers)
        self._fit = fit
        table = self.query_one(DataTable)
        table.clear(columns=True)
        for header, (_, width) in zip(headers, fit, strict=True):
            table.add_column(header, width=width)

    def focus_pane(self, pane: str) -> None:
        if pane not in PANES:
            self.log_line(f"[yellow]no {pane} pane in v0[/]")
            return
        # The log and the files pane are transient: each opens when focused and
        # closes when focus leaves, so escape gets rid of it. The info pane is
        # toggled by hand (`z i`), and focusing must not argue with that.
        self.query_one("#log-pane").display = pane == "log"
        files = self.query_one("#files-pane")
        if files.display != (pane == "files"):
            files.display = pane == "files"
            side = self.query_one("#side")
            if side.display != (files.display or self.query_one("#info-pane").display):
                self.apply_split()  # the side column appears or goes: the list resizes
            self.refresh_files()
        self.mode = pane
        self.refresh_hints()
        for name in PANES:
            self.query_one(f"#{name}-pane").set_class(name == pane, "pane-active")
        self.refresh_status()

    def on_click(self, event: events.Click) -> None:
        """A click focuses the pane it lands in, the way `1`-`4` do. It lands on
        the innermost widget — the info pane's text, not the pane — so look up."""
        panes = {f"{name}-pane": name for name in PANES}
        hit = event.widget.ancestors_with_self if event.widget else []
        if pane := next((panes[widget.id] for widget in hit if widget.id in panes), None):
            self.pending = ()  # a chord begun in one pane is not the next pane's
            self.hide_which_key()
            self.focus_pane(pane)

    # ── data ────────────────────────────────────────────────────────────────

    @property
    def current(self) -> Document | None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        return self.rows[row] if 0 <= row < len(self.rows) else None

    @property
    def targets(self) -> list[Document]:
        """What a batch-aware command acts on: every mark, or the cursor."""
        if self.marks:
            return [d for d in self.docs if library.doc_id(d) in self.marks]
        doc = self.current
        return [doc] if doc else []

    def apply_sort(self) -> None:
        self.docs = library.sort(
            self.docs,
            self.sort_key,
            reverse=self.sort_reverse,
            tiebreak=self.cfg.get("list.sort_tiebreak"),
            null_ordering=self.cfg.get("list.null_ordering", "last"),
        )

    def refresh_rows(self, keep: str | None = None) -> None:
        """Rebuild the table, keeping the cursor on the same document."""
        keep = keep or (library.doc_id(self.current) if self.current else None)
        table = self.query_one(DataTable)
        self.sync_columns()
        table.clear()
        # What to light up in each cell: the positive, unqualified terms. A
        # negated term matched nothing here by definition, and a qualified one
        # may have matched a field that is not on screen.
        lit = [
            term.text
            for term in library.parse_query(self.narrow_query, self.cfg.get("query.aliases", {}))
            if not term.negate and not term.field
        ]
        height = max(1, self.cfg.get("list.row_height", 1))
        # DataTable cells are Rich, not CSS-styled, so the theme's colour has to
        # be fetched by hand — `theme_variables` is the same table the CSS reads.
        mark_style = f"bold {self.theme_variables.get('accent', '')}".strip()
        warn_style = f"bold {self.theme_variables.get('error', '')}".strip()
        self._paint = (lit, height, mark_style, warn_style)
        for doc in self.rows:
            table.add_row(*self.row_cells(doc), height=height)
        if keep is not None:
            row = next((i for i, d in enumerate(self.rows) if library.doc_id(d) == keep), 0)
            table.move_cursor(row=row)
        self.refresh_info()
        self.refresh_status()
        self.refresh_hints()

    def row_cells(self, doc: Document) -> list[Text]:
        """One document's row, painted with what the last `refresh_rows` decided."""
        lit, height, mark_style, warn_style = self._paint
        marked = library.doc_id(doc) in self.marks
        marker = ui.glyph("warning") if doctor.peek(doc) else " "
        title = self.title_column
        cells = []
        for index, (column, width) in enumerate(self._fit):
            text = self.cell_text(doc, column)
            if index == 0:
                text = f"{ui.glyph('mark') if marked else ' '} {text}"
            flex = column is title
            room = width - MARKER_WIDTH if flex else width
            # Only the title column wraps: it is the one holding a title long
            # enough to need a second line, and giving every column the extra
            # rows would just pad the table out.
            if height > 1 and flex:
                text = library.fit_lines(text, room, height).replace("\n", "\n" + " " * MARKER_WIDTH)
            else:
                text = library.fit(text, room)
            cell = Text()
            if flex:  # the exception marker, in its own colour
                cell.append(f"{marker} ", style=warn_style)
            cell.append(text, style=mark_style if marked else "")
            if lit:
                cell.highlight_words(lit, "reverse", case_sensitive=False)
            cells.append(cell)
        return cells

    def refresh_row(self, index: int) -> None:
        """Redraw one row in place. A mark toggle changes one row, and rebuilding
        all of them cost 0.4 s per `space` at 5000 documents."""
        table = self.query_one(DataTable)
        for column, cell in enumerate(self.row_cells(self.rows[index])):
            table.update_cell_at(Coordinate(index, column), cell)

    @work(exclusive=True, group="narrow")
    async def apply_narrow(self, query: str) -> None:
        """Debounced, cancellable: an exclusive worker replaces the pending one."""
        await asyncio.sleep(self.cfg.get("query.narrow_debounce_ms", 60) / 1000)
        self.refilter(query)

    def refilter(self, query: str | None = None) -> None:
        """Re-apply the current narrow filter to the scoped set, right now."""
        query = self.narrow_query if query is None else query
        self.narrow_query = query
        self.visual = None  # a range over rows that just changed means nothing
        # Before `rows` changes: afterwards the cursor's index points into the
        # new list, at whichever document happens to sit there now.
        keep = library.doc_id(self.current) if self.current else None
        docs = self.docs
        if self.marked_only:
            docs = [d for d in docs if library.doc_id(d) in self.marks]
        if self.doctor_only:
            docs = [d for d in docs if doctor.peek(d, self.doctor_only)]
        self.rows = self.matching(docs, query)
        self.refresh_rows(keep)

    def matching(self, docs: list[Document], query: str) -> list[Document]:
        """The narrow filter, configured: the `/` prompt and `mark.query` alike."""
        return library.narrow(
            docs,
            query,
            self.cfg.get("query.narrow_fields", ["title"]),
            self.cfg.get("query.narrow_mode", "substring"),
            self.cfg.get("query.aliases", {}),
        )

    # ── chrome ──────────────────────────────────────────────────────────────

    def refresh_info(self) -> None:
        pane = self.query_one("#info", Static)
        if self.current is None:
            pane.update("[dim]no document[/]")
            return
        # The flattened view, so `venue` is the conference or journal name rather
        # than the city the stored key actually holds, and `kind` is available.
        doc = library.flatten(self.current)
        strip = self.cfg.get("list.strip_latex", True)

        def show(value: Any) -> str:
            text = library.display(value)
            # `ui.literal`, not `escape`: Textual drops `[Extended]` from a title
            # and neither escape helper touches it — see the note on `literal`.
            return ui.literal(library.strip_latex(text) if strip else text)

        # The icon sits left of the right-aligned label, so the icons line up in
        # their own column instead of drifting with the length of each field name.
        lines: list[str | tuple[str, str]] = [f"[$primary bold]{show(doc.get('title', '<untitled>'))}[/]", ""]
        for key in INFO_FIELDS:
            if doc.get(key):
                value = self.info_value(key, doc[key], show)
                lines.append((f"[dim]{ui.glyph(f'field.{key}')} {key:>8}[/]  ", value))
        files = place.entries(self.current)
        if files:
            # The icon belongs to the section, not to every row: repeating it once
            # per entry says nothing, while a missing file is worth shouting about.
            lines += ["", f"[dim]{ui.glyph('field.files')}    files ({len(files)})[/]"]
            for entry in files:
                # the real document, not the flattened copy: resolve() needs the
                # main folder to turn a relative `files` entry into a path
                ok = place.resolve(self.current, entry).exists()
                mark = " " if ok else f"[$error]{ui.glyph('warning')}[/]"
                lines.append((f"  {mark} ", ui.literal(str(entry))))
        lines += self.doctor_lines(self.current)
        if doctor.STALE:  # queued by this redraw's `peek`s: the rows' and this pane's
            self.recheck()
        # Last, so a long one never pushes the files or the findings out of view.
        if doc.get("abstract"):
            text = " ".join(str(doc["abstract"]).split())  # fetched ones keep the source's hard wraps
            lines += ["", f"[dim]{ui.glyph('field.abstract')} abstract[/]", show(text)]
        self.refresh_files()
        if library.doc_id(self.current) != self._info_of:  # another document reads from its top
            self._info_of = library.doc_id(self.current)
            self.query_one("#info-pane").scroll_home(animate=False)
        pane.update(
            Content("\n").join(
                self.hang(*line) if isinstance(line, tuple) else Content.from_markup(line) for line in lines
            )
        )

    def refresh_files(self) -> None:
        """The files pane: the current document's `files` in stored order — the
        first entry is "the" file — with the cursor on `file_index`."""
        pane = self.query_one("#files-pane", OptionList)
        if not pane.display:
            return
        doc = self.current
        if doc is None or library.doc_id(doc) != self._files_of:
            self.file_index = 0  # another document: start at its first file
        self._files_of = library.doc_id(doc) if doc else None
        entries = place.entries(doc) if doc else []
        self.file_index = max(0, min(self.file_index, len(entries) - 1))
        patterns = self.cfg.get("files.kind_patterns", {})
        pane.clear_options()
        for entry in entries:
            path = place.resolve(doc, entry)
            mark = " " if path.exists() else ui.glyph("warning")
            kind = next((name for name, globs in patterns.items() if any(fnmatch(path.name, g) for g in globs)), "")
            row = Text(f"{mark} {entry}")
            if kind:
                row.append(f"  {kind}", style="dim")
            pane.add_option(row)
        if not entries:
            pane.add_option(Text("no files — f a attaches one", style="dim"))
        pane.highlighted = self.file_index if entries else None

    def hang(self, label: str, value: str) -> Content:
        """`label` then `value`, the value wrapped so its continuation lines sit
        under it — a long URL or doctor message otherwise ran back under the
        label, to the pane's left edge. Both are markup."""
        head, body = Content.from_markup(label), Content.from_markup(value)
        width = self.query_one("#info").size.width
        if width < 16:  # not laid out yet
            return head + body
        indent = head.cell_length
        if indent > width // 2:  # a long label — a doctor check's name: the value goes underneath
            head, indent = head.rstrip() + Content("\n    "), 4
        return head + Content("\n" + " " * indent).join(body.wrap(width - indent))

    def info_value(self, key: str, value: Any, show: Any) -> str:
        """One info-pane value, in the colour its meaning deserves.

        Four fields say something a plain string does not: tags are a set of
        things, a reading status is a state, a rating is a quantity, and `ref`
        is the handle you yank into a `.tex`. Everything else is text, and
        colouring it too would leave nothing standing out.
        """
        if key == "tags":
            tags = value if isinstance(value, list) else str(value).split(", ")
            return "[dim] · [/]".join(f"[$secondary]{ui.literal(str(tag))}[/]" for tag in tags)
        if key == "reading_status":
            colour = READING_COLOURS.get(str(value).casefold(), "dim")
            return f"[{colour}]{show(value)}[/]"
        if key == "rating":
            stars = max(0, min(5, int(value))) if str(value).isdigit() else 0
            return f"[$warning]{ui.glyph('star') * stars}[/][dim]{ui.glyph('star_empty') * (5 - stars)}[/]"
        if key == "ref":
            return f"[$accent]{show(value)}[/]"
        return show(value)

    def doctor_lines(self, doc: Document) -> list[str | tuple[str, str]]:
        """This document's findings, from the cache the startup scan fills.

        A clean document says nothing — the section is an exception marker, and
        a permanent "no findings" would cost a line on every row to say so. An
        edited document shows its old findings until `recheck` has looked again;
        one the scan never reached reads "not checked", never clean.
        """
        found = doctor.peek(doc)
        if found == []:
            return []
        if found is None:
            return ["", f"[dim]{ui.glyph('field.doctor')}   doctor[/]", "  [dim]not checked[/]"]
        head: list[str | tuple[str, str]] = ["", f"[dim]{ui.glyph('field.doctor')}   doctor ({len(found)})[/]"]
        # `literal`: a finding quotes the offending value, and a `[...]`-shaped
        # one is otherwise swallowed by the markup
        return [
            *head,
            *(
                (
                    f"  [$error]{ui.glyph('warning')}[/] [$warning]{ui.literal(f.name)}[/]  ",
                    ui.literal(f.msg) + ("" if f.fix_action else "  [dim](no fix)[/]"),
                )
                for f in found
            ),
        ]

    @work(thread=True, exclusive=True, group="doctor")
    def scan_library(self) -> None:
        """Fill the findings cache in the background, once, at startup.

        ~1.7 s for 754 documents, which is why it cannot live in `refresh_info`
        and why it is a thread: the per-document checks are pure CPU and papis
        I/O, and nothing here touches the UI until the call back onto the app.
        """
        checks = self.cfg.get("doctor.checks", [])
        for doc in list(self.docs):
            doctor.scan(doc, checks)
        self.call_from_thread(self.refresh_rows)  # the markers live in the rows

    @work(thread=True, group="recheck")
    def recheck(self) -> None:
        """Re-check the documents written since their scan, off the redraw that
        found them, then repaint. Not exclusive: two at once take different
        documents from `doctor.STALE`, and each repaints only if it took any."""
        if doctor.recheck():
            self.call_from_thread(self.refresh_rows)

    def blocks(self, segments: list[tuple[str, str, str]], *, rightwards: bool) -> tuple[str, int]:
        """Coloured blocks joined by powerline separators, plus their cell width.

        The separator is drawn in the *previous* block's colour on the *next*
        one's, which is what makes the arrow read as one block flowing into the
        next rather than as a glyph sitting between them. `STATUS_BG` stands in
        for the bar itself at whichever end the group does not reach.
        """
        arrow = ui.glyph("sep_right" if rightwards else "sep_left")
        out, width, behind = [], 0, STATUS_BG
        for text, foreground, background in segments:
            ahead = background if rightwards else behind
            out.append(f"[{(behind if rightwards else background)} on {ahead}]{arrow}[/]")
            out.append(f"[{foreground} on {background}]{ui.literal(text)}[/]")  # a query is text
            width += cell_len(arrow) + cell_len(text)
            behind = background
        if rightwards:  # close the group back into the bar
            out.append(f"[{behind} on {STATUS_BG}]{arrow}[/]")
            width += cell_len(arrow)
        return "".join(out), width

    def refresh_status(self) -> None:
        """A lualine-shaped bar: mode block, scope and narrow on the left, the
        counts on the right. Colours are theme variables, so every theme gets
        the same shape in its own palette.

        When it all does not fit, the counts never yield. The sort block goes
        first: it changes only when asked (`S`), and a shown column's header
        carries it. Then the scope and the narrow are cut with an ellipsis, the
        longer one first, and last the left group itself.
        """
        visible_marks = sum(1 for d in self.rows if library.doc_id(d) in self.marks)
        arrow = ui.glyph("sort_desc" if self.sort_reverse else "sort_asc")
        mode = (f" {self.mode.upper()} ", "$background", "$primary")
        if self.visual is not None:
            mode = (f" {'UNMARK' if self.visual[1] else 'VISUAL'} ", "$background", "$accent")

        def left(cap: int) -> list[tuple[str, str, str]]:
            """The left group, each query cut to `cap` cells."""
            out = [mode, (f" {ui.glyph('scope')} {library.fit(self.scope_query or '*', cap)} ", "$text", "$panel")]
            if self.narrow_query:
                out.append((f" / {library.fit(self.narrow_query, cap)} ", "$background", "$secondary"))
            if self.doctor_only:
                out.append((f" {ui.glyph('warning')} doctor ", "$background", "$warning"))
            return out

        pending = [(f" {' '.join(self.pending)}- ", "$background", "$warning")] if self.pending else []
        sort = (f" {self.sort_key} {arrow} ", "$text", "$panel")
        marked = f"{len(self.marks)} marked ({visible_marks} visible) " if self.marks else ""
        counts = (f" {marked}{len(self.rows)} shown / {len(self.docs)} total ", "$background", "$primary")
        longest = max(cell_len(self.scope_query or "*"), cell_len(self.narrow_query))
        layouts = [
            (left(longest), [*pending, sort, counts]),
            *((left(cap), [*pending, counts]) for cap in range(longest, 0, -1)),
            ([mode], [*pending, counts]),
            ([], [*pending, counts]),
        ]
        # shortcut: below the counts' own width (~42 cells, with marks) they run off too
        for head_blocks, tail_blocks in layouts:  # the fullest that fits
            head, used = self.blocks(head_blocks, rightwards=True)
            tail, tail_width = self.blocks(tail_blocks, rightwards=False)
            if used + tail_width <= self.size.width:
                break
        gap = max(0, self.size.width - used - tail_width)
        self.query_one("#status-bar", Static).update(f"{head}[on {STATUS_BG}]{' ' * gap}[/]{tail}")

    def refresh_hints(self) -> None:
        if not self.km.option("hint_bar", True):
            return
        if self.mode == "files":
            wanted = ["doc.open", "files.rename", "files.repoint", "files.detach", "files.reorder", "files.normalize"]
        elif self.mode == "info":
            wanted = ["nav.down", "info.scroll", "doc.edit", "app.undo", "pane.cycle"]
        elif self.mode == "log":
            wanted = ["log.scroll", "pane.cycle", "app.log"]
        elif self.marks:
            wanted = ["doc.tag", "doc.merge", "export.bibtex", "mark.clear", "files.relocate", "doc.delete"]
        else:
            wanted = ["doc.open", "query.narrow", "query.scope", "mark.toggle", "doc.add", "help.show"]
        # Only what exists: the hint bar is the one piece of chrome that is always
        # on screen, and advertising a key that logs "not implemented yet" there
        # is worse than showing one hint fewer.
        wanted = [name for name in wanted if name in commands.REGISTRY]
        hints = []
        for name in wanted[: self.km.option("hint_bar_max", 6)]:
            keys = self.km.for_command(self.mode, name)
            if keys:
                cmd = commands.REGISTRY.get(name)
                hints.append(f"[$accent bold]{keys}[/] [dim]{cmd.desc if cmd else name}[/]")
        self.query_one("#hint-bar", Static).update(" " + "   ".join(hints))

    def log_line(self, message: str) -> None:
        """User-visible operation log. Batch outcomes belong here, not in a toast.

        `[red]` and friends are rewritten into the theme's own colours on the way
        past, so a command says `[red]failed:[/]` — which reads as intent at the
        call site — and still comes out in the palette the rest of the app uses.
        Translating here rather than at forty call sites is also what keeps the
        Rich/Textual markup split from leaking into `actions.py`.
        """
        # Escape everything, then let the call site's own tags back through:
        # what a command interpolates (a title, a path, `[modes.files]`) is text.
        message = escape(message)
        for tag in LOG_MARKUP:
            message = message.replace(escape(tag), tag)
        for tag, variable in LOG_TAGS.items():
            if tag in message:
                message = message.replace(tag, f"[{self.theme_variables.get(variable, '')}]")
        self.query_one(LogPane).add(message)

    # ── prompt ──────────────────────────────────────────────────────────────

    def open_prompt(self, kind: str, placeholder: str, value: str = "") -> None:
        self.prompt_kind = kind
        prompt = self.query_one(Input)
        prompt.placeholder = placeholder
        prompt.value = value
        prompt.add_class("open")
        prompt.focus()
        self._recall, self._draft = 0, ""

    def recall(self, step: int) -> None:
        """Up (`step` 1) walks back through the query history, down forward;
        past the newest entry is whatever was being typed before. Only entries
        that start with that, and are not it, are walked: typing first is the
        prefix search."""
        prompt = self.query_one(Input)
        if self._recall == 0:
            self._draft = prompt.value
        past = [line for line in actions.history(self) if line.startswith(self._draft) and line != self._draft]
        self._recall = max(0, min(len(past), self._recall + step))
        prompt.value = past[-self._recall] if self._recall else self._draft
        prompt.cursor_position = len(prompt.value)

    def close_prompt(self) -> None:
        self.prompt_kind = ""
        prompt = self.query_one(Input)
        prompt.remove_class("open")
        prompt.blur()
        self.refresh_status()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.prompt_kind == "narrow":
            self.apply_narrow(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        kind, value = self.prompt_kind, event.value
        self.close_prompt()
        try:
            if kind in actions.HISTORY_KINDS:
                actions.remember(self, value)
            if kind == "narrow":
                self.apply_narrow(value)
            elif kind == "scope":
                actions.scope(self, value)
            elif kind:
                actions.prompt_result(self, kind, value)
        except Exception as exc:  # as in run_command: a broken prompt must not end the session
            logger.exception(f"{kind} prompt failed: {exc}")

    def on_data_table_row_highlighted(self, _: Any) -> None:
        # A highlight queued before teardown still gets delivered after the
        # widgets are gone, and `refresh_info` would raise NoMatches on the way
        # out. Rebuilding the columns during a layout flip is what queues it.
        if self.query(DataTable):
            self.refresh_info()
