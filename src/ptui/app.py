"""The Textual app: panes, key dispatch, and the state every command mutates.

Key handling deliberately bypasses Textual's own bindings. Every keypress goes
through `keymap` -> `commands.REGISTRY`, so keys.toml is the only place bindings
live and the help, hint bar and which-key panel all derive from the same table.
"""

from __future__ import annotations

import asyncio
from typing import Any

import papis.format
from loguru import logger
from papis.document import Document
from rich.cells import cell_len
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import DataTable, Input, RichLog, Static

from ptui import (
    actions,
    commands,
    config,
    keymap,
    library,
    place,
    ui,
)

PANES = ("list", "info", "log")

MIN_FLEX = 12
"""Cells the flexible column keeps. Below this a fixed column is dropped instead —
author plus a stubby title beats a wide title and no idea who wrote it."""

INFO_FIELDS = ("author", "year", "venue", "ref", "doi", "url", "tags", "notes", "reading_status")
"""Info-pane fields, in order. Each needs a `field.<name>` entry in `ui.GLYPHS`."""


class ListTable(DataTable):
    """The document list. Refits its columns whenever its own size changes.

    The app-level resize handler is too early — widget geometry is only real
    once layout has run, and that is exactly when this fires.
    """

    def on_resize(self, _: events.Resize) -> None:
        self.app.refresh_rows()  # type: ignore[attr-defined]


class PtuiApp(App[None]):
    """Layout CSS lives here; themes only recolour it."""

    CSS = """
    Screen { layers: base which; }
    #panes { height: 1fr; }
    #list-pane { width: 1fr; }
    #info-pane { width: 1fr; padding: 0 1; }
    #log-pane { height: 10; display: none; }
    #hint-bar, #status-bar { height: 1; }
    #prompt { display: none; height: 1; border: none; padding: 0 1; }
    #prompt.open { display: block; }
    #which-key {
        layer: which; display: none; dock: bottom; height: auto; width: auto;
        offset: 2 -3; padding: 0 1; border: round $panel;
    }
    #which-key.open { display: block; }
    .pane-active { border-left: thick $accent; }
    """

    def __init__(self, cfg: config.Config, km: keymap.Keymap) -> None:
        theme = cfg.get("ui.theme", "")
        css = next(
            (d / f"{theme}.tcss" for d in cfg.theme_dirs if (d / f"{theme}.tcss").is_file()), None
        )
        super().__init__(css_path=css)
        self.cfg = cfg
        self.km = km
        ui.use_icons(cfg.get("ui.icons", False))  # every glyph goes through ui.glyph
        self.mode = "list"
        self.pending: keymap.Chord = ()
        self.docs: list[Document] = []  # the scoped set
        self.rows: list[Document] = []  # the scoped set after narrowing
        self.marks: set[str] = set()
        self.scope_query = cfg.get("general.startup_query", "")
        self.narrow_query = ""
        self.marked_only = False
        self.sort_key, self.sort_reverse = self._default_sort()
        self.prompt_kind = ""
        self.side_by_side = cfg.get("ui.layout", "vertical") == "vertical"
        self.split = cfg.get("ui.split_ratio", 0.6)
        self._fit: list[tuple[dict[str, Any], int]] = []
        self._fit_state: tuple[list[tuple[dict[str, Any], int]], list[str]] | None = None
        self._which_timer: Any = None

    # ── setup ───────────────────────────────────────────────────────────────

    def _default_sort(self) -> tuple[str, bool]:
        presets = self.cfg.get("list.sort_presets", [])
        preset = next((p for p in presets if p.get("default")), presets[0] if presets else {})
        return preset.get("key", "time-added"), preset.get("dir", "desc") == "desc"

    def compose(self) -> ComposeResult:
        table = ListTable(id="list-pane", cursor_type="row", zebra_stripes=False)
        table.can_focus = False  # all keys go through our dispatcher
        info = VerticalScroll(Static(id="info"), id="info-pane")
        info.can_focus = False
        with Container(id="panes"):  # apply_split() owns the layout direction
            yield table
            yield info
        yield RichLog(id="log-pane", markup=True, max_lines=self.cfg.get("log.max_entries", 500))
        yield Static(id="which-key")
        yield Static(id="hint-bar")
        yield Static(id="status-bar")
        yield Input(id="prompt")

    def on_mount(self) -> None:
        self.apply_split()
        self._setup_logging()
        for problem in self.km.unknown_commands:
            self.log_line(f"[yellow]keys.toml:[/] {problem}")
        actions.reload(self)
        self.focus_pane("list")

    def _setup_logging(self) -> None:
        logger.remove()
        pane = self.query_one(RichLog)
        level = self.cfg.get("log.level", "info").upper()
        logger.add(
            lambda m: pane.write(m.rstrip()), level=level, format="{time:HH:mm:ss} {message}"
        )
        log_file = self.cfg.as_path("log.file")
        if log_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            logger.add(log_file, level=level, rotation="1 MB", retention=3)

    # ── key dispatch ────────────────────────────────────────────────────────

    async def on_key(self, event: Any) -> None:
        if len(self.screen_stack) > 1:
            return  # a modal owns the keyboard
        if self.prompt_kind:
            if event.key == "escape":
                self.close_prompt()
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
        try:
            commands.run(name, self, args)
        except KeyError:
            self.log_line(f"[yellow]{name} is not implemented yet[/]")
        except Exception as exc:  # a broken command must not kill the session
            logger.exception(f"{name} failed: {exc}")
            self.log_line(f"[red]{name} failed:[/] {exc}")

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
        panel.update(
            "\n".join(
                f"[bold]{b.keys[len(' '.join(self.pending)) + 1 :]}[/]  {b.desc or b.cmd}"
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

    def apply_split(self) -> None:
        """Size the panes for the current layout. The only place that sets either.

        Both dimensions are set every time: a leftover width from the previous
        layout is exactly why `z z` used to do nothing visible. The list takes
        `split` of the axis, or the whole window when it is the only pane left.
        """
        self.query_one("#panes").styles.layout = "horizontal" if self.side_by_side else "vertical"
        table = self.query_one(DataTable)
        info = self.query_one("#info-pane")
        share = f"{int(self.split * 100)}%" if info.display else "1fr"
        table.styles.width, table.styles.height = (
            (share, "1fr") if self.side_by_side else ("1fr", share)
        )
        info.styles.width = info.styles.height = "1fr"
        self.call_after_refresh(self.refresh_rows)  # columns refit to the new width

    # ── columns ─────────────────────────────────────────────────────────────

    def cell_text(self, doc: Document, column: dict[str, Any]) -> str:
        """What a column renders for a document, before it is cut to width.

        A column with `glyph = "type"` renders its value through the `type.*`
        family instead of printing it, so `inproceedings` becomes one cell.
        """
        text = papis.format.format(column["format"], library.flatten(doc), default="")
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

    def header(self, column: dict[str, Any]) -> str:
        """`Year ↓` when the list is sorted on that column's own field."""
        if column["format"].strip() != self.sort_format:
            return column["title"]
        return f"{column['title']} {ui.glyph('sort_desc' if self.sort_reverse else 'sort_asc')}"

    def natural_width(self, index: int, column: dict[str, Any]) -> int:
        """What a fixed column asks for: its p90 over the whole narrowed set,
        capped by the configured width and never narrower than its header.

        Computed over `app.rows`, not the visible window — sizing to what is on
        screen makes the column jitter while scrolling.
        """
        wanted = library.p90([self.cell_text(doc, column) for doc in self.rows])
        if index == 0:
            wanted += 2  # the mark glyph and its space
        return min(column["width"], max(wanted, cell_len(self.header(column))))

    def fit_columns(self) -> list[tuple[dict[str, Any], int]]:
        """The columns that fit the pane, with the width each one gets.

        Configured widths are a ceiling, not a reservation: a column takes the
        p90 of what it actually holds. The `width = 0` column absorbs whatever
        is left. A fixed column that would starve it is dropped instead — that
        is how `Tags` disappears on a narrow terminal and comes back on a wide
        one, rather than the list scrolling sideways.
        """
        table = self.query_one(DataTable)
        spec = self.cfg.get("list.columns", [])
        pad = table.cell_padding * 2
        room = table.size.width - 2 - table.scrollbar_size_vertical  # border, scrollbar
        flex = next((i for i, column in enumerate(spec) if not column["width"]), None)
        widths: dict[int, int] = {}
        for index, column in enumerate(spec):
            if index == flex:
                continue
            want = self.natural_width(index, column)
            floor = MIN_FLEX + pad if flex is not None else 0
            if room - want - pad < floor:
                continue
            room -= want + pad
            widths[index] = want
        if flex is not None:
            widths[flex] = max(MIN_FLEX, room - pad)
        return [(spec[index], widths[index]) for index in sorted(widths)]

    def sync_columns(self) -> None:
        """Rebuild the table's columns when the fit or the headers changed — a
        resize, `z z`, or a new sort key that moves the direction arrow."""
        fit = self.fit_columns()
        headers = [self.header(column) for column, _ in fit]
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
        self.mode = pane
        for name in PANES:
            self.query_one(f"#{name}-pane").set_class(name == pane, "pane-active")
        self.refresh_status()

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
        for doc in self.rows:
            marked = library.doc_id(doc) in self.marks
            cells = []
            for index, (column, width) in enumerate(self._fit):
                text = self.cell_text(doc, column)
                if index == 0:
                    text = f"{ui.glyph('mark') if marked else ' '} {text}"
                text = library.fit(text, width)
                # ponytail: bold marks the row; per-theme colouring needs Rich
                # styles that CSS classes cannot reach into DataTable cells.
                cells.append(Text(text, style="bold" if marked else ""))
            table.add_row(*cells)
        if keep is not None:
            row = next((i for i, d in enumerate(self.rows) if library.doc_id(d) == keep), 0)
            table.move_cursor(row=row)
        self.refresh_info()
        self.refresh_status()
        self.refresh_hints()

    @work(exclusive=True, group="narrow")
    async def apply_narrow(self, query: str) -> None:
        """Debounced, cancellable: an exclusive worker replaces the pending one."""
        await asyncio.sleep(self.cfg.get("query.narrow_debounce_ms", 60) / 1000)
        self.refilter(query)

    def refilter(self, query: str | None = None) -> None:
        """Re-apply the current narrow filter to the scoped set, right now."""
        query = self.narrow_query if query is None else query
        self.narrow_query = query
        docs = self.docs
        if self.marked_only:
            docs = [d for d in docs if library.doc_id(d) in self.marks]
        self.rows = library.narrow(
            docs,
            query,
            self.cfg.get("query.narrow_fields", ["title"]),
            self.cfg.get("query.narrow_mode", "fuzzy"),
        )
        self.refresh_rows()

    # ── chrome ──────────────────────────────────────────────────────────────

    def refresh_info(self) -> None:
        doc = self.current
        pane = self.query_one("#info", Static)
        if doc is None:
            pane.update("[dim]no document[/]")
            return
        strip = self.cfg.get("list.strip_latex", True)

        def show(value: Any) -> str:
            text = library.display(value)
            return library.strip_latex(text) if strip else text

        # The icon sits left of the right-aligned label, so the icons line up in
        # their own column instead of drifting with the length of each field name.
        lines = [f"[bold]{show(doc.get('title', '<untitled>'))}[/]", ""]
        for key in INFO_FIELDS:
            if doc.get(key):
                lines.append(f"[dim]{ui.glyph(f'field.{key}')} {key:>8}[/]  {show(doc[key])}")
        files = doc.get("files", [])
        if files:
            # The icon belongs to the section, not to every row: repeating it once
            # per entry says nothing, while a missing file is worth shouting about.
            lines += ["", f"[dim]{ui.glyph('field.files')}    files[/]"]
            for entry in files:
                ok = place.resolve(doc, entry).exists()
                mark = " " if ok else f"[red]{ui.glyph('warning')}[/]"
                lines.append(f"  {mark} {entry}")
        pane.update("\n".join(lines))

    def refresh_status(self) -> None:
        visible_marks = sum(1 for d in self.rows if library.doc_id(d) in self.marks)
        parts = [
            f"{ui.glyph('scope')} scope: {self.scope_query or '*'}",
            f"narrow: {self.narrow_query or '-'}",
            f"sort: {self.sort_key} {ui.glyph('sort_desc' if self.sort_reverse else 'sort_asc')}",
            f"{len(self.marks)} marked ({visible_marks} visible) / {len(self.rows)} shown / {len(self.docs)} total",
        ]
        if self.pending:
            parts.append(f"[bold]{' '.join(self.pending)}-[/]")
        self.query_one("#status-bar", Static).update(" | ".join(parts))

    def refresh_hints(self) -> None:
        if not self.km.option("hint_bar", True):
            return
        wanted = (
            ["doc.delete", "doc.tag", "export.bibtex", "mark.clear", "files.relocate"]
            if self.marks
            else ["doc.open", "query.narrow", "query.scope", "mark.toggle", "doc.add", "help.show"]
        )
        hints = []
        for name in wanted[: self.km.option("hint_bar_max", 6)]:
            keys = self.km.for_command(self.mode, name)
            if keys:
                cmd = commands.REGISTRY.get(name)
                hints.append(f"[bold]{keys}[/] {cmd.desc if cmd else name}")
        self.query_one("#hint-bar", Static).update("  ".join(hints))

    def log_line(self, message: str) -> None:
        """User-visible operation log. Batch outcomes belong here, not in a toast."""
        self.query_one(RichLog).write(message)

    # ── prompt ──────────────────────────────────────────────────────────────

    def open_prompt(self, kind: str, placeholder: str, value: str = "") -> None:
        self.prompt_kind = kind
        prompt = self.query_one(Input)
        prompt.placeholder = placeholder
        prompt.value = value
        prompt.add_class("open")
        prompt.focus()

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
        if kind == "narrow":
            self.apply_narrow(value)
        elif kind == "scope":
            actions.scope(self, value)
        elif kind:
            actions.prompt_result(self, kind, value)

    def on_data_table_row_highlighted(self, _: Any) -> None:
        self.refresh_info()
