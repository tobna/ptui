"""Command implementations. Every one is registered in `commands.REGISTRY`.

Handlers take the app first and are plain functions — nothing here imports the
app module, so `app.py` can import this at load time to register everything.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import partial
from pathlib import Path
from typing import Any

from loguru import logger
from textual.widgets import DataTable

from ptui import clip, commands, config, doctor, fetch, library, merge, place, safewrite, ui, undo
from ptui.commands import REGISTRY, command

# ── navigation ──────────────────────────────────────────────────────────────


def _table(app: Any) -> DataTable:
    """Find the document list.

    Args:
        app: The running `PtuiApp`.

    Returns:
        The list's `DataTable`.
    """
    return app.query_one(DataTable)


def _move(app: Any, delta: int) -> None:
    """Move the cursor of the focused pane, stretching a visual range along the way.

    Args:
        app: The running `PtuiApp`.
        delta: Rows to move; negative moves up. Clamped to the list.
    """
    if app.mode == "files":  # the same keys, the pane's own cursor
        app.file_index = max(0, app.file_index + delta)
        app.refresh_files()
        return
    table = _table(app)
    row = max(0, min(table.row_count - 1, table.cursor_row + delta))
    table.move_cursor(row=row)
    if app.visual is not None:
        _visual_extend(app)
    app.refresh_info()
    app.refresh_status()


@command("nav.down", "move down")
def nav_down(app: Any, count: int = 1) -> None:
    """Move down.

    Args:
        app: The running `PtuiApp`.
        count: How many rows.
    """
    _move(app, count)


@command("nav.up", "move up")
def nav_up(app: Any, count: int = 1) -> None:
    """Move up.

    Args:
        app: The running `PtuiApp`.
        count: How many rows.
    """
    _move(app, -count)


@command("nav.top", "first document")
def nav_top(app: Any) -> None:
    """Go to the first document.

    Args:
        app: The running `PtuiApp`.
    """
    _move(app, -_table(app).row_count)


@command("nav.bottom", "last document")
def nav_bottom(app: Any) -> None:
    """Go to the last document.

    Args:
        app: The running `PtuiApp`.
    """
    _move(app, _table(app).row_count)


@command("nav.page_down", "page down")
def nav_page_down(app: Any) -> None:
    """Move down by a page.

    Args:
        app: The running `PtuiApp`.
    """
    _move(app, max(1, _table(app).size.height - 2))


@command("nav.page_up", "page up")
def nav_page_up(app: Any) -> None:
    """Move up by a page.

    Args:
        app: The running `PtuiApp`.
    """
    _move(app, -max(1, _table(app).size.height - 2))


def _scroll(app: Any, pane: str, up: bool, lines: int = 0, end: bool = False) -> None:
    """Scroll a pane by `lines`, or half the pane when that is 0; `end` goes all the way.

    Args:
        app: The running `PtuiApp`.
        pane: CSS selector of the pane.
        up: Scroll towards the top.
        lines: Lines to move; 0 means half the pane.
        end: Go all the way to the top or the bottom.
    """
    widget = app.query_one(pane)
    if end:
        (widget.scroll_home if up else widget.scroll_end)(animate=False)
        return
    step = lines or max(1, widget.size.height // 2)
    widget.scroll_relative(y=-step if up else step, animate=False)


@command("info.scroll", "scroll the info pane")
def info_scroll(app: Any, up: bool = False) -> None:
    """Half a pane, from wherever the keyboard is — an abstract is read from the list.

    Args:
        app: The running `PtuiApp`.
        up: Scroll towards the top.
    """
    _scroll(app, "#info-pane", up)


@command("log.scroll", "scroll the log")
def log_scroll(app: Any, up: bool = False, lines: int = 0, end: bool = False) -> None:
    """Scroll the log by half a pane, or `lines` of it.

    `j`/`k` in the log move one, or with `end` to the top (`up`) or the bottom, `g g` and `G` as in vim.

    Args:
        app: The running `PtuiApp`.
        up: Scroll towards the top.
        lines: Lines to move; 0 means half the pane.
        end: Go all the way to the top or the bottom.
    """
    _scroll(app, "#log-pane", up, lines, end)


_CITE = re.compile(r"\\\w*cite\w*\*?(?:\[[^]]*\])*\{([^}]*)\}")


@command("nav.goto", "go to a citekey")
def nav_goto(app: Any, ref: str | None = None) -> None:
    r"""Move the cursor to the document whose `ref` is exactly this.

    That is a citekey pasted from a `.tex`, `\cite{...}` and all; a list of keys goes to the first.

    Without an exact match it says so, and still goes where there is only one
    candidate: the one ref equal but for case, else the one ref containing it —
    or, of several, the one the narrowed list shows.

    A narrow that hides it is dropped, since jumping is the point; a document
    outside the scope is only reported, because changing the scope is `s`.

    Args:
        app: The running `PtuiApp`.
        ref: The citekey, or `None` to prompt for one.
    """
    if ref is None:
        app.open_prompt("goto", "citekey (\\cite{...} works too)")
        return
    found = _CITE.search(ref)
    key = (found.group(1) if found else ref).split(",")[0].strip()
    if not key:
        return
    doc = next((candidate for candidate in app.docs if str(candidate.get("ref", "")) == key), None)
    if doc is None:
        refs = {library.doc_id(candidate): str(candidate.get("ref", "")) for candidate in app.docs}
        near = [
            candidate for candidate in app.docs if refs[library.doc_id(candidate)].casefold() == key.casefold()
        ] or [candidate for candidate in app.docs if key.casefold() in refs[library.doc_id(candidate)].casefold()]
        shown = {library.doc_id(candidate) for candidate in app.rows}
        if (
            len(near) > 1
            and len(visible := [candidate for candidate in near if library.doc_id(candidate) in shown]) == 1
        ):
            near = visible  # the one the narrowed list shows is the one meant
        if len(near) != 1:
            some = (
                f": {len(near)} refs contain it, {', '.join(str(candidate.get('ref')) for candidate in near[:3])}"
                if near
                else ""
            )
            app.log_line(f"[yellow]no ref {key} in scope {app.scope_query or '*'}{some}[/]")
            return
        doc = near[0]
        app.log_line(f"[yellow]no ref {key}; went to {doc.get('ref')}, the one that matches it[/]")
    _jump(app, doc)
    if app.visual is not None:  # a jump stretches the range like any move
        _visual_extend(app)


# ── panes ───────────────────────────────────────────────────────────────────


@command("pane.focus", "focus a pane")
def pane_focus(app: Any, pane: str) -> None:
    """Focus a pane.

    Args:
        app: The running `PtuiApp`.
        pane: A name from `app.PANES`.
    """
    app.focus_pane(pane)


@command("pane.cycle", "next pane")
def pane_cycle(app: Any, back: bool = False) -> None:
    """Focus the next pane.

    Args:
        app: The running `PtuiApp`.
        back: Go to the previous pane instead.
    """
    from ptui.app import PANES

    index = PANES.index(app.mode) if app.mode in PANES else 0
    app.focus_pane(PANES[(index - 1 if back else index + 1) % len(PANES)])


@command("pane.toggle", "show/hide a pane")
def pane_toggle(app: Any, pane: str) -> None:
    """Show or hide a pane.

    Args:
        app: The running `PtuiApp`.
        pane: A name from `app.PANES`.
    """
    if pane == "list":
        app.log_line("[yellow]the list pane cannot be hidden[/]")
        return
    if pane in ("files", "log"):  # transient: shown is focused
        app.focus_pane("list" if app.mode == pane else pane)
        return
    try:
        widget = app.query_one(f"#{pane}-pane")
    except Exception:
        app.log_line(f"[yellow]no {pane} pane in v0[/]")
        return
    widget.display = not widget.display
    if not widget.display and app.mode == pane:
        app.focus_pane("list")
    app.apply_split()


@command("pane.toggle_layout", "horizontal/vertical split")
def pane_toggle_layout(app: Any) -> None:
    """Flip between the side-by-side and the stacked layout, ending automatic choice.

    Args:
        app: The running `PtuiApp`.
    """
    app.layout_auto = False  # an explicit choice wins over `ui.layout = "auto"`
    app.side_by_side = not app.side_by_side
    app.apply_split()


@command("pane.resize", "adjust the split")
def pane_resize(app: Any, delta: float) -> None:
    """Move the split between the list and the side panes.

    Args:
        app: The running `PtuiApp`.
        delta: Change of the list's share, kept between 0.1 and 0.9.
    """
    app.split = min(0.9, max(0.1, app.split + delta))
    app.apply_split()


@command("app.log", "operation log")
def app_log(app: Any) -> None:
    """Show the log pane, or leave it.

    Args:
        app: The running `PtuiApp`.
    """
    app.focus_pane("list" if app.query_one("#log-pane").display else "log")


# ── queries ─────────────────────────────────────────────────────────────────


def scope(app: Any, query: str) -> bool:
    """Run a papis query and rebuild the scoped set. Marks survive by design.

    Args:
        app: The running `PtuiApp`.
        query: The papis query as typed; aliases are expanded here.

    Returns:
        False when papis rejected the query and the old scope is still on screen, else True.
    """
    aliases = app.config.get("query.aliases", {})
    expanded = library.expand_aliases(query, aliases)
    try:
        app.docs = library.scope(expanded, app.config.get("general.library"))
    except Exception as error:
        # The scope stays what it was, and so does the status bar: it must
        # name the query whose results are on screen, not the one that failed.
        app.log_line(f"[red]query failed:[/] {query}: {(str(error).splitlines() or [repr(error)])[0]}")
        app.query_one("#log-pane").display = True
        if query and not app.docs:  # the startup query: the library beats an empty screen
            app.scope_query = ""
            scope(app, "")
        return False
    app.scope_query = query
    if expanded != query:
        app.log_line(f"[dim]scope -> {expanded}[/]")
    app.apply_sort()
    app.refilter()
    return True


# ── saved searches ──────────────────────────────────────────────────────────


def _saved_file(app: Any) -> dict[str, dict[str, Any]]:
    """What `query.save` wrote.

    JSON, in the state dir: ptui never rewrites `config.toml`, and the standard library cannot
    write TOML anyway.

    Args:
        app: The running `PtuiApp`.

    Returns:
        Search name to its `scope`, `sort` and `reverse`; empty when the file is missing or broken.
    """
    path = app.config.as_path("query.saved_file")
    try:
        return json.loads(path.read_text()) if path else {}
    except (OSError, ValueError):
        return {}


def saved_searches(app: Any) -> dict[str, dict[str, Any]]:
    """Combine `[query.saved]` from `config.toml` with what `query.save` wrote.

    A name saved from the app wins. A plain string is a scope with no sort.

    Args:
        app: The running `PtuiApp`.

    Returns:
        Search name to its table, which holds at least `scope`.
    """
    searches = {
        name: value if isinstance(value, dict) else {"scope": str(value)}
        for name, value in app.config.get("query.saved", {}).items()
    }
    searches.update(_saved_file(app))
    return searches


def _write_saved(app: Any, saved: dict[str, dict[str, Any]]) -> None:
    """Write the saved searches to `query.saved_file`.

    Args:
        app: The running `PtuiApp`.
        saved: Search name to its table.

    Raises:
        ValueError: When `query.saved_file` is not set.
    """
    path = app.config.as_path("query.saved_file")
    if path is None:
        raise ValueError("query.saved_file is not set")
    _write_json(path, saved)


def _write_json(path: Path, data: Any) -> None:
    """Write JSON atomically, creating the parent folder.

    Args:
        path: The file to replace.
        data: Anything `json.dumps` takes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.ptui.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _state(app: Any) -> tuple[Path | None, dict[str, Any]]:
    """Read the session state file.

    Args:
        app: The running `PtuiApp`.

    Returns:
        `(path, state)`: the path is `None` while `general.persist_state` is off, and the state maps a
        library's name to its session, empty when the file is missing or broken.
    """
    path = app.config.as_path("general.state_file") if app.config.get("general.persist_state", False) else None
    try:
        return path, json.loads(path.read_text()) if path else {}
    except (OSError, ValueError):
        return path, {}


def restore_state(app: Any) -> str | None:
    """Bring back what `general.persist_state` remembered for this library.

    That is the scope and sort the last session ended on, and the `papis_id` its cursor was on. An
    explicit scope, `startup_query` or one on the command line, wins over the remembered one.

    An explicit scope — `startup_query`, or one on the command line — wins over the remembered
    one.

    Args:
        app: The running `PtuiApp`.

    Returns:
        The `papis_id` the cursor was on, or `None`.
    """
    import papis.config

    state = _state(app)[1].get(papis.config.get_lib_name(), {})
    if not app.scope_query:
        app.scope_query = state.get("scope", "")
    if state.get("sort"):
        app.sort_key, app.sort_reverse = state["sort"], bool(state.get("reverse"))
    return state.get("cursor")


def save_state(app: Any, cursor: str | None) -> None:
    """Remember this library's scope, sort and cursor for the next session.

    Args:
        app: The running `PtuiApp`.
        cursor: The `papis_id` the cursor is on, or `None`.
    """
    import papis.config

    path, state = _state(app)
    if path is None:
        return
    here = {"scope": app.scope_query, "sort": app.sort_key, "reverse": app.sort_reverse, "cursor": cursor}
    state[papis.config.get_lib_name()] = here
    try:
        _write_json(path, state)
    except OSError as error:  # the app is closing: the log file is all that is left
        logger.warning(f"session state not saved: {error}")


@command("query.save", "save the current search")
def query_save(app: Any, name: str | None = None) -> None:
    """Save the scope and the sort under a name, for `g s`.

    The narrow is not part of it: it is the per-keystroke layer, and a saved search is a place.

    Args:
        app: The running `PtuiApp`.
        name: The search's name, or `None` to prompt for one.
    """
    if name is None:
        app.open_prompt("save", "name for this search (scope + sort)")
        return
    if app.config.as_path("query.saved_file") is None:
        app.log_line("[red]nowhere to save:[/] query.saved_file is not set in config.toml")
        return
    saved = _saved_file(app)
    replaced = name in saved_searches(app)
    saved[name] = {"scope": app.scope_query, "sort": app.sort_key, "reverse": app.sort_reverse}
    _write_saved(app, saved)
    app.log_line(f"{'replaced' if replaced else 'saved'} {name}: {app.scope_query or '*'}, by {app.sort_key}")


HISTORY_KINDS = ("scope", "narrow", "mark", "unmark")
"""The prompts that take a query, and so share `query.history_file`."""
HISTORY_MAX = 500


def history(app: Any) -> list[str]:
    """Submitted queries, oldest first. One per line: a prompt is one line.

    Args:
        app: The running `PtuiApp`.

    Returns:
        The queries, empty when the file is unset or unreadable.
    """
    path = app.config.as_path("query.history_file")
    try:
        return path.read_text().splitlines() if path else []
    except OSError:
        return []


def remember(app: Any, query: str) -> None:
    """Add a query to the history: newest last, each query once.

    Running one again moves it to the end.

    Args:
        app: The running `PtuiApp`.
        query: The query as submitted; a blank one is not kept.
    """
    path = app.config.as_path("query.history_file")
    if path is None or not query.strip():
        return
    past = [*[line for line in history(app) if line != query][-(HISTORY_MAX - 1) :], query]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.ptui.tmp")
        temporary.write_text("\n".join(past) + "\n")
        temporary.replace(path)
    except OSError as error:  # a read-only state dir costs the history, not the query
        app.log_line(f"[yellow]query history not saved: {error}[/]")


@command("query.load", "saved searches")
def query_load(app: Any, name: str | None = None) -> None:
    r"""Apply a saved search: its scope and, if it has one, its sort.

    With no name, a picker; there `shift+enter` forgets a search `\ s` saved.

    Args:
        app: The running `PtuiApp`.
        name: The search's name, or `None` for the picker.
    """
    saved = saved_searches(app)
    if name is not None:
        if name not in saved:
            app.log_line(f"[yellow]no saved search {name}[/]")
            return
        entry = saved[name]
        if "sort" in entry:
            app.sort_key = entry["sort"]
            app.sort_reverse = bool(entry.get("reverse", app.sort_reverse))
        if scope(app, str(entry.get("scope", ""))):
            app.log_line(f"search: {name}")
        return

    if not saved:
        app.log_line("[yellow]no saved searches yet — \\ s saves this one[/]")
        return
    items = [
        ui.Item(label=label, value=label, hint=f"{entry.get('scope') or '*'}  {entry.get('sort', '')}".strip())
        for label, entry in saved.items()
    ]

    def chosen(label: str, forget: bool) -> None:
        """Apply or forget the picked search.

        Args:
            label: The search's name.
            forget: `shift+enter` was used: forget it instead.
        """
        if not forget:
            query_load(app, label)
            return
        mine = _saved_file(app)
        if label not in mine:
            app.log_line(f"[yellow]{label} is in config.toml; remove it there[/]")
            return
        del mine[label]
        _write_saved(app, mine)
        app.log_line(f"forgot {label}")

    ui.pick(app, items, title="Saved searches (shift+enter forgets)")(chosen)


def prompt_result(app: Any, kind: str, value: str) -> None:
    """Dispatch a prompt whose result is not a query.

    Args:
        app: The running `PtuiApp`.
        kind: What the prompt was for, as `open_prompt` got it; some kinds carry a value after a colon.
        value: The text submitted.
    """
    if kind == "add":
        add_form(app, Path(value).expanduser())
    elif kind == "attach":
        if value.strip():
            files_attach(app, value.strip())
    elif kind == "set":
        # `key value with spaces` — the value is the rest of the line, not a
        # shell word: this is a prompt, and quoting a title would be absurd.
        field, _, text = value.strip().partition(" ")
        if field:
            doc_set(app, field, text.strip())
    elif kind in ("tag", "untag"):
        (doc_tag if kind == "tag" else doc_untag)(app, value)
    elif kind == "save":
        if value.strip():
            query_save(app, value.strip())
    elif kind.startswith("repoint:"):
        if value.strip():
            files_repoint(app, int(kind.removeprefix("repoint:")), value.strip())
    elif kind == "goto":
        if value.strip():
            nav_goto(app, value)
    elif kind in ("mark", "unmark"):
        if value.strip():
            mark_query(app, value, unmark=kind == "unmark")
    elif kind.startswith("cmdline:"):
        cmdline_run(app, kind.removeprefix("cmdline:"), value)
    elif kind.startswith("import:"):
        _import(app, kind.removeprefix("import:"), value)
    else:
        app.log_line(f"[yellow]unhandled prompt {kind!r}: {value}[/]")


def _import(app: Any, source: str, uri: str) -> None:
    """Fetch metadata off the UI thread, then open the form with what came back.

    A thread worker, not a blocking call: an importer is an HTTP round trip and
    some of them download a PDF, which would freeze the whole app for seconds
    with no way to tell it had not simply hung.

    Args:
        app: The running `PtuiApp`.
        source: The source's name from `[add.sources]`.
        uri: What the user typed: an id, a path, a URL or a search.
    """
    if not uri.strip():
        return
    if source == "bib":
        _bib_pick(app, Path(uri).expanduser())
        return
    app.log_line(f"[dim]fetching from {source}…[/]")

    def job() -> None:
        """Fetch in the worker thread, handing the result back to the app's own."""
        try:
            if source == "crossref":  # a search: every hit, to choose from
                found = [(hit, []) for hit in fetch.crossref(uri)]
            else:
                found = [fetch.from_url(uri) if source == "url" else fetch.fetch(source, uri)]
        except Exception as error:
            app.call_from_thread(app.log_line, f"[red]{source} failed:[/] {error}")
            return
        app.call_from_thread(_fetched, app, source, found)

    app.run_worker(job, thread=True, group="import", exit_on_error=False)


def _fetched(app: Any, source: str, found: list[tuple[dict[str, Any], list[Path]]]) -> None:
    """Open the add form on what a fetch found, asking first when it found several.

    Args:
        app: The running `PtuiApp`.
        source: The source's name.
        found: `(metadata, downloaded files)` per hit, best first.
    """
    if len(found) > 1:

        def chosen(index: int, _invert: bool) -> None:
            """Continue with the picked hit.

            Args:
                index: The hit's position, or `NONE_OF_THESE`.
                _invert: The picker's `shift+enter` flag; unused.
            """
            if index == NONE_OF_THESE:
                app.log_line(f"{source}: none of them; nothing added")
            else:
                _fetched(app, source, [found[index]])

        ui.pick(app, _hit_items([data for data, _ in found]), title=f"{source} found {len(found)} — which one?")(chosen)
        return
    data, files = found[0]
    got = ", ".join(key for key in ("title", "author", "year", "doi") if data.get(key))
    app.log_line(f"{source}: {got or 'metadata'}" + (f" + {len(files)} file(s)" if files else ""))
    # papis downloads to a temp file; `place()` moves it where the rules say.
    add_form(app, files[0] if files else None, data)


def _bib_pick(app: Any, path: Path) -> None:
    """A `.bib` usually holds many entries, so choose one rather than importing the file.

    Bulk import is a different feature — see TODO E.

    Args:
        app: The running `PtuiApp`.
        path: The `.bib` file.
    """
    try:
        entries = fetch.bib_entries(path)
    except Exception as error:
        app.log_line(f"[red].bib failed:[/] {error}")
        return
    if len(entries) == 1:
        add_form(app, None, entries[0])
        return
    items = [
        ui.Item(
            label=library.display(entry.get("title", "<untitled>")),
            value=index,
            hint=str(entry.get("ref") or entry.get("year") or ""),
        )
        for index, entry in enumerate(entries)
    ]
    ui.pick(app, items, title=f"{len(entries)} entries in {path.name}")(
        lambda index, _inverted: add_form(app, None, entries[index])
    )


def _hit_summary(hit: dict[str, Any]) -> str:
    """Year, first author, venue: what tells two papers of one title apart.

    Args:
        hit: One record of document keys.

    Returns:
        The parts it has, joined by ` · `.
    """
    names = merge._authors(hit) or [{}]
    first = names[0].get("family") if isinstance(names[0], dict) else None
    return " · ".join(str(part) for part in (hit.get("year"), first, library.venue(hit)) if part)


NONE_OF_THESE = -1
"""The value of a "none of these" row: `None` is the picker's own "no current
value", so a row holding it opens highlighted."""


def _hit_items(hits: list[dict[str, Any]]) -> list[ui.Item]:
    """A search's hits, best first, then "none of these".

    Args:
        hits: Records of document keys, best first.

    Returns:
        One picker row per hit, valued by index, then the `NONE_OF_THESE` row.
    """
    items = [
        ui.Item(label=library.display(hit.get("title", "<untitled>")), value=index, hint=_hit_summary(hit))
        for index, hit in enumerate(hits)
    ]
    return [*items, ui.Item(label="none of these", value=NONE_OF_THESE)]


@command("query.scope", "search (papis query)")
def query_scope(app: Any, q: str | None = None) -> None:
    """Run a papis query, or open the prompt for one.

    Args:
        app: The running `PtuiApp`.
        q: The query, or `None` for the prompt. Named by keys.toml's `args`, so public.
    """
    if q is None:
        app.open_prompt("scope", "papis query", app.scope_query)
    else:
        scope(app, q)


@command("query.narrow", "narrow (instant)")
def query_narrow(app: Any, q: str | None = None) -> None:
    """Narrow the list, or open the prompt to.

    Args:
        app: The running `PtuiApp`.
        q: The narrow query, or `None` for the prompt. Named by keys.toml's `args`, so public.
    """
    if q is None:
        app.open_prompt("narrow", "narrow", app.narrow_query)
    else:
        app.apply_narrow(q)


@command("query.clear", "clear the narrow filter")
def query_clear(app: Any) -> None:
    """Clear the narrow filter.

    Args:
        app: The running `PtuiApp`.
    """
    app.refilter("")


@command("app.reload", "reload library")
def reload(app: Any, rescan: bool = False) -> None:
    """Re-run the scope query against a fresh database.

    `rescan` throws papis's *on-disk* cache away as well, which is what an undo
    needs: `clear_cached` only drops the in-process handle, so a folder that has
    just come back from the trash stays invisible until papis walks the library
    again. Deleting is the cheap direction — papis wrote the document out of the
    cache when it went — and restoring is the one that has to pay for the walk.

    Args:
        app: The running `PtuiApp`.
        rescan: Throw papis's on-disk cache away too.
    """
    import papis.database

    if rescan:
        papis.database.get(app.config.get("general.library") or None).clear()
    papis.database.clear_cached()
    scope(app, app.scope_query)


# ── sorting ─────────────────────────────────────────────────────────────────


@command("sort.by", "sort by a key")
def sort_by(app: Any, key: str, reverse: bool | None = None) -> None:
    """Sort the list by a key.

    Args:
        app: The running `PtuiApp`.
        key: Dotted path to sort by.
        reverse: Sort descending; `None` uses the key's preset direction.
    """
    if reverse is None:
        presets = {preset["key"]: preset for preset in app.config.get("list.sort_presets", [])}
        reverse = presets.get(key, {}).get("dir", "asc") == "desc"
    app.sort_key, app.sort_reverse = key, reverse
    app.apply_sort()
    app.refilter()


@command("sort.reverse", "reverse sort")
def sort_reverse(app: Any) -> None:
    """Reverse the current sort.

    Args:
        app: The running `PtuiApp`.
    """
    sort_by(app, app.sort_key, not app.sort_reverse)


@command("sort.picker", "sort by…")
def sort_picker(app: Any) -> None:
    """Open the sort picker: presets first, then keys discovered in the library.

    `enter` applies the key's own default direction, `shift+enter` inverts it: a single global reverse
    flag is wrong half the time (author ascending, date descending).

    Args:
        app: The running `PtuiApp`.
    """
    presets = app.config.get("list.sort_presets", [])
    items = [
        ui.Item(
            label=f"{preset.get('label', preset['key'])} {ui.glyph('sort_desc' if preset.get('dir') == 'desc' else 'sort_asc')}",
            value=preset["key"],
            hint=preset["key"],
        )
        for preset in presets
    ]
    if app.config.get("list.sort_discover_keys", True):
        known = {preset["key"] for preset in presets}
        # ponytail: discovered from the documents already in memory. Add the
        # worker + cache generation from SPEC if a big library makes this drag.
        items += [
            ui.Item(label=key, value=key, hint="discovered")
            for key in library.discover_keys(app.docs)
            if key not in known
        ]

    def apply(key: str, inverted: bool) -> None:
        """Sort by the picked key.

        Args:
            key: The picked sort key.
            inverted: `shift+enter` was used: flip the key's own direction.
        """
        directions = {preset["key"]: preset.get("dir", "asc") for preset in presets}
        reverse = directions.get(key, "asc") == "desc"
        sort_by(app, key, reverse != inverted)

    ui.pick(app, items, title="Sort by", current=app.sort_key)(apply)


@command("lib.switch", "switch library")
def lib_switch(app: Any, name: str | None = None) -> None:
    """Switch to another library, or open the picker.

    Args:
        app: The running `PtuiApp`.
        name: The library's name, or `None` for the picker.
    """
    import papis.config

    def apply(library_name: str, _inverted: bool = False) -> None:
        """Make a library current, dropping the marks, and load it.

        Args:
            library_name: The library to switch to.
            _inverted: The picker's `shift+enter` flag; unused.

        Raises:
            ValueError: When papis has no such library.
        """
        library.use(library_name)  # raises on an unknown name, before anything changed
        app.config.data["general"]["library"] = library_name
        if why := undo.require_git(app.config, library_dir(app)):
            app.log_line(f"[yellow]{why}[/]")
        app.marks.clear()
        reload(app)
        app.log_line(f"library: {library_name}")

    if name:
        apply(name)
        return
    current = app.config.get("general.library") or papis.config.get_lib_name()
    items = [ui.Item(label=known_library, value=known_library) for known_library in papis.config.get_libs()]
    ui.pick(app, items, title="Library", current=current)(apply)


@command("doc.move", "move to another library")
def doc_move(app: Any, to: str = "") -> None:
    """Run `papis mv` into another library.

    Each folder keeps its name, both papis databases learn of it, and under `use-git` both libraries get
    a commit. Not undoable: the way back is the same command from the other library, so a batch confirms
    first, like every other batch.

    Args:
        app: The running `PtuiApp`.
        to: The target library; empty opens a picker.
    """
    import papis.config

    docs = list(app.targets)
    if not docs:
        return
    if not to:
        current = papis.config.get_lib_name()
        others = [library_name for library_name in papis.config.get_libs() if library_name != current]
        if not others:
            app.log_line("[yellow]papis knows no other library to move to[/]")
            return
        ui.pick(
            app,
            [ui.Item(label=library_name, value=library_name) for library_name in others],
            title=f"Move {len(docs)} document(s) to",
        )(lambda library_name, _inverted: doc_move(app, library_name))
        return
    try:
        target = Path(papis.config.get_lib_from_name(to).path)
    except Exception as error:  # papis's InvalidLibraryError, worded for its command line
        app.log_line(f"[red]library {to}:[/] {' '.join(map(str, error.args))}")
        return
    if len(docs) == 1:
        _move_apply(app, docs, to, target)
        return
    items = [ui.Item(label=f"move {len(docs)} documents to {to}", value=True), ui.Item(label="cancel", value=False)]
    ui.pick(app, items, title=f"Move to {to}?")(
        lambda ok, _inverted: _move_apply(app, docs, to, target) if ok else None
    )


def _move_apply(app: Any, docs: list[Any], to: str, target: Path) -> None:
    """Move documents to another library, one at a time; a clash stops only its own document.

    Args:
        app: The running `PtuiApp`.
        docs: The documents to move.
        to: The target library's name.
        target: The target library's folder.
    """
    import papis.commands.mv
    import papis.database

    # papis's mv adds the document to the target's index; an index that does not
    # exist yet is built by scanning the folder it has just moved into, and then
    # holds it twice. Build it first, from the folder as it is now.
    papis.database.get(library_name=to).get_all_documents()
    git = bool(app.config.papis("edit.use_git", "use-git"))
    moved = 0
    for doc in docs:
        destination = target / Path(doc.get_main_folder()).name
        try:
            papis.commands.mv.run(doc, str(destination), target_library=to, git=git)
        except Exception as error:  # a clash for one document must not stop the rest
            app.log_line(f"[red]{doc.get('ref', '')} not moved:[/] {error}")
            continue
        app.marks.discard(library.doc_id(doc))
        moved += 1
    reload(app)
    app.log_line(f"moved {moved} document(s) to {to}")


@command("lib.backfill_dates", "stamp missing time-added")
def lib_backfill_dates(app: Any) -> None:
    """Give every document in scope that lacks `time-added` the mtime of its `info.yaml`.

    Only `papis add`'s command line writes the key, so a library carries
    documents stamped by it and hundreds without — and "recently added"
    sorts the whole silent majority into one undifferentiated tail. The mtime
    is approximate (a later edit moves it) but it is the only ordering on disk,
    and it is monotone enough for a library nobody has bulk-rewritten. More
    than one document confirms first, as any batch does.

    Args:
        app: The running `PtuiApp`.
    """
    planned = [
        # a stamp the file gained while the question was open is kept
        (doc, lambda old, stamp=library.stamp(Path(doc.get_info_file()).stat().st_mtime): old or stamp)
        for doc in app.docs
        if not doc.get(library.TIME_ADDED)
    ]
    if not planned:
        app.log_line("every document in scope already has time-added")
        return
    _confirm_batch(app, len(planned), "backfill time-added", lambda: _set_apply(app, planned, library.TIME_ADDED))


# ── marks ───────────────────────────────────────────────────────────────────


@command("mark.toggle", "mark/unmark")
def mark_toggle(app: Any) -> None:
    """Mark or unmark the document under the cursor.

    Args:
        app: The running `PtuiApp`.
    """
    doc = app.current
    if doc is None:
        return
    app.visual = None  # every other mark command ends a range, or its next move undoes them
    app.marks.symmetric_difference_update({library.doc_id(doc)})
    app.refresh_row(_table(app).cursor_row)
    if app.config.get("marks.advance", True):
        _move(app, 1)
    app.refresh_status()
    app.refresh_hints()


def _mark(app: Any, docs: list[Any], unmark: bool) -> None:
    """Mark or unmark a set of documents, ending any visual range.

    Args:
        app: The running `PtuiApp`.
        docs: The documents.
        unmark: Take them off the marks instead.
    """
    ids = {library.doc_id(doc) for doc in docs}
    app.visual = None
    if unmark:
        app.marks -= ids
    else:
        app.marks |= ids
    app.refresh_rows()


@command("mark.all_filtered", "mark all filtered")
def mark_all_filtered(app: Any, unmark: bool = False) -> None:
    """Mark every row on screen.

    `unmark` takes the same set back off. SPEC: every way of marking has a way of unmarking what it
    marked.

    Args:
        app: The running `PtuiApp`.
        unmark: Take them off the marks instead.
    """
    _mark(app, app.rows, unmark)


@command("mark.query", "mark by query")
def mark_query(app: Any, q: str | None = None, unmark: bool = False) -> None:
    """Mark every row on screen that matches a narrow query, without touching the view.

    Inside a narrow it refines it, so nothing hidden gets marked, and `m a` would need the query typed
    into the narrow and dropped again.

    Args:
        app: The running `PtuiApp`.
        q: The narrow query, or `None` for the prompt. Named by keys.toml's `args`, so public.
        unmark: Take the matches off the marks instead.
    """
    if q is None:
        app.open_prompt("unmark" if unmark else "mark", f"narrow query — {'unmark' if unmark else 'mark'} what matches")
        return
    found = app.matching(app.rows, q)
    _mark(app, found, unmark)
    app.log_line(f"{'unmarked' if unmark else 'marked'} {len(found)} document(s) matching {q}")


@command("mark.invert", "invert marks")
def mark_invert(app: Any) -> None:
    """Flip the mark of every row on screen.

    Args:
        app: The running `PtuiApp`.
    """
    app.visual = None
    app.marks ^= {library.doc_id(doc) for doc in app.rows}
    app.refresh_rows()


@command("mark.clear", "clear marks")
def mark_clear(app: Any) -> None:
    """Clear every mark.

    Args:
        app: The running `PtuiApp`.
    """
    app.visual = None
    app.marks.clear()
    app.refresh_rows()


@command("mark.show_only", "toggle marked-only")
def mark_show_only(app: Any) -> None:
    """Toggle showing only the marked documents.

    Args:
        app: The running `PtuiApp`.
    """
    app.marked_only = not app.marked_only
    app.refilter()


@command("visual.start", "visual mark")
def visual_start(app: Any, unmark: bool = False) -> None:
    """Mark every row between here and the cursor, as it moves.

    So what is drawn marked is what is marked, and any verb acts on it. Pressed again, or escape, ends
    the range and keeps the marks.

    Args:
        app: The running `PtuiApp`.
        unmark: Unmark the range instead.
    """
    if app.visual is not None:
        _visual_end(app)
        return
    if app.current is None:
        return
    app.visual = (library.doc_id(app.current), unmark, set(app.marks))
    _visual_extend(app)
    app.refresh_status()
    app.refresh_hints()


def _visual_extend(app: Any) -> None:
    """Marks become what they were at `v`, plus (or minus) the range now.

    Args:
        app: The running `PtuiApp`.
    """
    anchor, unmark, before = app.visual
    ids = [library.doc_id(doc) for doc in app.rows]
    if anchor not in ids:  # deleted under the range: nothing left to stretch from
        _visual_end(app)
        return
    low, high = sorted((ids.index(anchor), _table(app).cursor_row))
    span = set(ids[low : high + 1])
    marks = before - span if unmark else before | span
    changed = app.marks ^ marks
    app.marks = marks
    for index, doc_id in enumerate(ids):
        if doc_id in changed:
            app.refresh_row(index)


def _visual_end(app: Any) -> None:
    """End the range, keeping the marks it left.

    Args:
        app: The running `PtuiApp`.
    """
    app.visual = None
    app.refresh_status()
    app.refresh_hints()


# ── document verbs ──────────────────────────────────────────────────────────


def files_of(app: Any, doc: Any) -> list[Path]:
    """Existing files of a document, main first.

    Kind is *inferred* from `files.kind_patterns` and never written to disk — a
    parallel `kind` key would desync with a script that appends to `files`.

    Args:
        app: The running `PtuiApp`.
        doc: The document to read.

    Returns:
        Its resolved paths, those matching no `files.kind_patterns` first.
    """
    patterns = [candidate for group in app.config.get("files.kind_patterns", {}).values() for candidate in group]
    paths = [place.resolve(doc, entry) for entry in place.entries(doc)]
    main = [candidate for candidate in paths if not any(fnmatch(candidate.name, pattern) for pattern in patterns)]
    return main + [candidate for candidate in paths if candidate not in main]


@command("doc.open", "open file")
def doc_open(app: Any, which: int | None = None) -> None:
    """Open a file of the document under the cursor.

    Args:
        app: The running `PtuiApp`.
        which: The position in `files_of`; `None` means the main file, or the files pane's cursor.
    """
    import papis.utils

    doc = app.current
    if doc is None:
        return
    paths = files_of(app, doc)
    if not paths:
        app.log_line("[yellow]no files attached[/]")
        return
    path = paths[which] if which is not None and which < len(paths) else paths[0]
    if which is None and app.mode == "files":  # the entry under the files pane's cursor
        path = place.resolve(doc, place.entries(doc)[app.file_index])
    if not path.exists():
        app.log_line(f"[red]missing file:[/] {path}")
        return
    papis.utils.open_file(str(path), wait=False)
    app.log_line(f"opened {path.name}")
    if app.config.get("general.track_opens", False):
        _touch_opened_at(app, doc)


def _touch_opened_at(app: Any, doc: Any) -> None:
    """Stamp `opened_at` on a document, for `general.track_opens`.

    Args:
        app: The running `PtuiApp`.
        doc: The document opened.
    """
    from datetime import datetime

    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        safewrite.edit(doc, lambda data: safewrite.put(data, "opened_at", stamp))
    except safewrite.StaleError:
        _reread(app, doc)


@command("files.open_pick", "open which file…")
def files_open_pick(app: Any) -> None:
    """Choose which file to open, when the document has several.

    Args:
        app: The running `PtuiApp`.
    """
    doc = app.current
    if doc is None:
        return
    paths = files_of(app, doc)
    if len(paths) < 2:  # nothing to choose between: open it, or say there is none
        doc_open(app)
        return
    items = [
        ui.Item(label=path.name, value=position, hint="" if path.exists() else "missing")
        for position, path in enumerate(paths)
    ]
    ui.pick(app, items, title="Open file")(lambda index, _invert: doc_open(app, which=index))


@command("doc.open_folder", "open folder")
def doc_open_folder(app: Any) -> None:
    """Open the document's folder.

    Args:
        app: The running `PtuiApp`.
    """
    import papis.api

    doc = app.current
    if doc is not None and doc.get_main_folder():
        papis.api.open_dir(doc.get_main_folder())


@command("doc.browse", "open URL/DOI")
def doc_browse(app: Any) -> None:
    """Open the document's URL or DOI.

    Args:
        app: The running `PtuiApp`.
    """
    import papis.commands.browse

    doc = app.current
    if doc is not None:
        app.log_line(f"browsing {papis.commands.browse.run(doc)}")


@command("doc.edit_raw", "edit info.yaml in $EDITOR")
def doc_edit_raw(app: Any) -> None:
    """Full-screen `$EDITOR` on `info.yaml` — never a pty embedded in a pane.

    Args:
        app: The running `PtuiApp`.
    """
    import papis.commands.edit

    doc = app.current
    if doc is None:
        return
    with app.suspend():
        papis.commands.edit.run(doc, wait=True)
    # Re-parse before trusting it: a stray tab or an unclosed quote leaves papis
    # unable to load the document at all, and it would otherwise show up much
    # later as a document that has silently lost every field.
    try:
        safewrite.read(Path(doc.get_info_file()))
    except Exception as error:
        key = _editor_key(app)
        again = f"press {key} again" if key else "run doc.edit_raw again"
        app.log_line(f"[red]invalid YAML, nothing reloaded:[/] {error}")
        app.log_line(f"[yellow]papis cannot load this document until it parses — {again}[/]")
        return
    _resync(app, doc)
    app.log_line(f"edited {doc.get('ref', doc.get('title', ''))}")


def _opens_editor(app: Any, other: bool) -> bool:
    """Whether `doc.edit` with this `other` is `$EDITOR` rather than the form.

    Args:
        app: The running `PtuiApp`.
        other: The binding's `other` argument.

    Returns:
        `True` when that binding opens `$EDITOR`.
    """
    return (app.config.get("edit.mode", "editor") == "structured") == other


def _editor_key(app: Any) -> str | None:
    """Find the keys that open `$EDITOR` from here.

    That is `doc.edit_raw`, or the `doc.edit` binding `edit.mode` sends there: `e` by default, `E` when
    it is `structured`.

    Args:
        app: The running `PtuiApp`.

    Returns:
        The keys, or `None` when nothing opens the editor.
    """
    for mode in (app.mode, "list"):
        for binding in app.keymap.modes.get(mode, {}).values():
            if binding.command == "doc.edit_raw" or (
                binding.command == "doc.edit" and _opens_editor(app, bool(binding.args.get("other")))
            ):
                return binding.keys
    return None


def _resync(app: Any, doc: Any) -> None:
    """Re-read a document papis itself wrote, and put it back in the index.

    Args:
        app: The running `PtuiApp`.
        doc: The document, reloaded in place.
    """
    import papis.database

    doc.load()
    papis.database.get().update(doc)
    app.refresh_rows()


def _reread(app: Any, doc: Any) -> None:
    """A write `safewrite` refused because `info.yaml` changed under it.

    This one document is read from disk right here, at once and without the library rescan `r`
    pays for, so running the verb again works.

    Args:
        app: The running `PtuiApp`.
        doc: The document the write was refused for.
    """
    _resync(app, doc)
    app.log_line(f"[yellow]{doc.get('ref') or 'info.yaml'} changed on disk; reloaded from disk; do it again[/]")


@command("doc.edit", "edit")
def doc_edit(app: Any, other: bool = False) -> None:
    """Open what `edit.mode` says `e` opens.

    `editor` is `$EDITOR` on the whole `info.yaml`, `structured` a form over `edit.structured_fields`,
    and `other` (`E`) opens the one `e` does not.

    The form cannot emit invalid YAML: every value goes through `library.typed`
    (papis's declared field types) and the safe write. Emptying a field removes
    it, and the whole edit is one undo step.

    Args:
        app: The running `PtuiApp`.
        other: Open the one `edit.mode` does not name.
    """
    import papis.document

    doc = app.current
    if doc is None:
        return
    if _opens_editor(app, other):
        doc_edit_raw(app)
        return
    doc.load()  # the form shows the file, not the copy loaded before a script changed it
    fields = tuple(app.config.get("edit.structured_fields") or ui.ADD_FIELDS)

    def text(field: str) -> str:
        """Read a field as the form shows it.

        Args:
            field: The field's key.

        Returns:
            The value as text, empty when the document lacks it.
        """
        if field == "author_list":
            return papis.document.author_list_to_author(doc) if doc.get("author_list") else str(doc.get("author") or "")
        value = doc.get(field)
        return "" if value is None else library.display(value)

    initial = {field: text(field) for field in fields}

    def confirm(data: dict[str, str] | None) -> None:
        """Apply the form's answer: the changed fields, or nothing on a cancel or a bad value.

        Args:
            data: Field name to its text, or `None` when cancelled.
        """
        if data is None:
            return
        changes: dict[str, Any] = {}
        try:
            for field, value in data.items():
                if value == initial[field]:
                    continue
                # functions of what the file holds when written (see `_fields_apply`)
                if field == "author_list":  # both keys, as papis's own add writes them
                    changes["author"] = value or None
                    changes["author_list"] = lambda old, value=value: _authors_from(value, old) or None
                else:
                    library.typed(field, value, doc.get(field))  # a bad number aborts here, before any write
                    changes[field] = lambda old, field=field, value=value: library.typed(field, value, old)
        except ValueError as error:  # `year = soon`: nothing is written
            app.log_line(f"[red]{error}[/]")
            return
        if changes:
            _fields_apply(app, doc, changes)

    ref = doc.get("ref") or doc.get("title", "")
    # the keys are the form's last line
    app.push_screen(ui.EditForm(f"Edit {ref}", fields, initial), ui.guarded("edit", confirm))


def _authors_from(line: str, before: Any) -> list[dict[str, Any]]:
    """Build papis's author list for an author line.

    Each author who is still named exactly as before keeps their entry whole: affiliation, ORCID and
    all, which a line of names cannot carry.

    Args:
        line: The authors as one line of text; empty means none.
        before: The list stored today, or anything else, which is ignored.

    Returns:
        One dict per author, in the line's order.
    """
    import papis.document

    known = {(author.get("family"), author.get("given")): author for author in before or [] if isinstance(author, dict)}
    fresh = papis.document.split_authors_name([line]) if line else []
    return [known.get((author.get("family"), author.get("given")), author) for author in fresh]


def _put_all(data: Any, values: dict[str, Any]) -> None:
    """Inside a `safewrite.edit`: every key of `values`, `None` removing it.

    Args:
        data: The ruamel map being edited.
        values: Key to its new value, `None` to remove the key.
    """
    for key, value in values.items():
        if value is None:
            safewrite.drop(data, key)
        else:
            safewrite.put(data, key, value)


def _fields_apply(app: Any, doc: Any, changes: dict[str, Any]) -> None:
    """Several keys of one document, one write and one undo step. `None` removes.

    A value may be a function of what the file holds under its key as it is
    rewritten, as in `_set_field`: a `files` list worked out from the copy
    loaded earlier dropped the file a script had attached since. Undo puts back
    what the write replaced, read from that same file; redo writes what it wrote.

    Args:
        app: The running `PtuiApp`.
        doc: The document to change.
        changes: Key to its new value, `None`, or a function of the stored value giving either.
    """
    replaced: dict[str, Any] = {}
    written: dict[str, Any] = {}

    def mutate(data: Any) -> None:
        """Work every value out from the file, remember both sides, and write.

        Args:
            data: The ruamel map of `info.yaml`.
        """
        for key, new in changes.items():
            replaced[key] = old = safewrite.plain(data.get(key))
            written[key] = new(old) if callable(new) else new
        _put_all(data, written)

    def write(values: dict[str, Any]) -> None:
        """Write one side of the edit, for its undo or its redo.

        Args:
            values: Key to its value, `None` to remove the key.
        """
        safewrite.edit(doc, lambda data: _put_all(data, values))
        _resort(app)

    try:
        safewrite.edit(doc, mutate)
    except safewrite.StaleError:
        _reread(app, doc)
        return
    except ValueError as error:  # a value the file's own type turns down: nothing was written
        app.log_line(f"[red]{error}[/]")
        return
    _resort(app)
    label = f"edit {', '.join(changes)} of {doc.get('ref', '')}"
    app.history.push(undo.Step(label, lambda: write(replaced), lambda: write(written)))
    app.log_line(label)


@command("doc.notes", "notes")
def doc_notes(app: Any) -> None:
    """Open (creating if needed) the document's notes file in `$EDITOR`.

    `papis.commands.edit.edit_notes` creates the file from papis's own template
    and writes the `notes` key itself — one of the few writes that does not go
    through `safewrite`, because it is papis's own API doing it, exactly as with
    `papis.commands.add.run`.

    Args:
        app: The running `PtuiApp`.
    """
    import papis.commands.edit

    doc = app.current
    if doc is None:
        return
    with app.suspend():
        papis.commands.edit.edit_notes(doc, git=bool(app.config.papis("edit.use_git", "use-git")))
    _resync(app, doc)
    app.log_line(f"notes: {doc.get('notes', 'unchanged')}")


@command("doc.set", "set any field")
def doc_set(app: Any, key: str | None = None, value: str | None = None) -> None:
    """Set one field on every target, through the safe write. Batch-aware.

    With no `key` it prompts — `c f` and a bare `:doc.set` land in the same
    place. An empty value **removes** the key, which is the only way to clear a
    field without opening `$EDITOR`. The value's type comes from
    `library.typed`, so `tags` becomes a list and `year` an int.

    Args:
        app: The running `PtuiApp`.
        key: The field's key, or `None` for the prompt.
        value: Its new text; `None` or empty removes the key.
    """
    if key is None:
        app.open_prompt("set", "key value  (no value clears the field)")
        return
    what = f"{key} = {value}" if value else f"remove {key}"
    _set_field(app, key, lambda old: library.typed(key, value or "", old), what)


def _set_field(app: Any, key: str, value_of: Callable[[Any], Any], what: str) -> None:
    """Plan one value per target, confirm a batch, then write.

    The single path every `c` verb takes — they differ only in how the value is worked out.

    `value_of` maps the value stored under `key` to the new one; `None` removes
    the key. It runs on what was loaded first, so a bad number aborts the batch
    before anything is written, and then inside each write on what the file
    holds by then: a tag a script added since ptui loaded the document is kept,
    not overwritten with the list ptui remembered.

    Args:
        app: The running `PtuiApp`.
        key: The field's key.
        value_of: Maps the stored value to the new one, `None` to remove the key.
        what: The change as text, for the confirmation.
    """
    targets = app.targets
    if not targets:
        return
    try:  # every value first, so a bad number aborts before anything is written
        for doc in targets:
            value_of(doc.get(key))
    except ValueError as error:
        app.log_line(f"[red]{error}[/]")
        return
    planned = [(doc, value_of) for doc in targets]
    _confirm_batch(app, len(targets), f"set {what}", lambda: _set_apply(app, planned, key))


def _confirm_batch(app: Any, count: int, doing: str, then: Callable[[], None]) -> None:
    """Run `then` for one document, ask first for more.

    SPEC: a batch confirms against the *total* count, marked documents the narrow
    hides included — `app.targets` already holds them.

    Args:
        app: The running `PtuiApp`.
        count: How many documents it touches.
        doing: The verb as text, for the question.
        then: Does the work; takes nothing.
    """
    if count <= 1:
        then()
        return
    # The picker is the confirm — ponytail: no preview list, the count and the
    # verb are the whole question here, unlike a delete.
    items = [ui.Item(label=f"{doing} on {count} documents", value=True), ui.Item(label="cancel", value=False)]
    ui.pick(app, items, title=f"{doing} — {count} documents?")(lambda ok, _invert: then() if ok else None)


def _write_field(
    app: Any, planned: list[tuple[Any, Any]], key: str, stale: list[Any] | None = None
) -> list[tuple[Any, Any, Any]]:
    """Write one key across documents, one file at a time.

    Each planned value is written as it is, or a planned function is applied to the value in the file as
    it is read for the write. A document whose file changed on disk goes to `_reread`, or, inside an
    undo
    or a redo, into `stale`, for it to end in `_raise_stale`.

    Args:
        app: The running `PtuiApp`.
        planned: `(doc, value or function of the stored value)` per document.
        key: The field's key.
        stale: Receives the documents whose file changed; `None` rereads them at once.

    Returns:
        `(doc, old, new)` for every write that landed, `old` as the file had it.
    """
    landed = []
    for doc, new in planned:
        seen: list[Any] = []

        def write(data: Any, new: Any = new, seen: list[Any] = seen) -> None:
            """Write the planned value and record both sides in `seen`.

            Args:
                data: The ruamel map of `info.yaml`.
                new: The planned value or function, bound per document.
                seen: Receives the old and the new value, bound per document.
            """
            old = safewrite.plain(data.get(key))
            value = new(old) if callable(new) else new
            seen.extend((old, value))
            if value is None:
                safewrite.drop(data, key)
            else:
                safewrite.put(data, key, value)

        try:
            safewrite.edit(doc, write)
        except safewrite.StaleError:
            if stale is None:
                _reread(app, doc)
            else:
                stale.append(doc)
        except Exception as error:
            app.log_line(f"[red]{key} on {doc.get('ref', '')} failed:[/] {error}")
        else:
            landed.append((doc, *seen))
    return landed


def _resort(app: Any) -> None:
    """Re-sort and redraw after an edit.

    The edit may have been to the key the list is sorted on: `c f year` while sorted by year has to move
    the row, not just repaint it. `refresh_rows` keeps the cursor on the document, so it follows
    wherever
    it lands.

    Args:
        app: The running `PtuiApp`.
    """
    app.apply_sort()
    app.refilter()


def _set_apply(app: Any, planned: list[tuple[Any, Any]], key: str) -> None:
    """Write, then push the step that puts the old values back.

    SPEC: metadata edits get a session-local in-memory undo stack *regardless
    of strategy* — this is the half of undo that has nothing to do with the
    trash or with git. The previous values are the ones each write replaced, read
    from the file it rewrote, so an undo restores exactly what was there — a tag
    a script added, or the key's absence — and redo writes what the edit wrote.

    Args:
        app: The running `PtuiApp`.
        planned: `(doc, value or function of the stored value)` per document.
        key: The field's key.
    """
    landed = _write_field(app, planned, key)
    pairs = [(doc, {key: (old, new)}) for doc, old, new in landed]
    if landed:
        label = f"set {key} on {len(landed)} document(s)"
        app.history.push(undo.Step(label, lambda: _rewrite(app, pairs, 0), lambda: _rewrite(app, pairs, 1)))
    _resort(app)
    app.log_line(f"set {key} on {len(landed)}/{len(planned)} document(s)")


def _rewrite(app: Any, fixed: list[tuple[Any, dict[str, tuple[Any, Any]]]], side: int) -> None:
    """Write one side of each `(replaced, written)` pair back into its file.

    A file changed on disk is left as it is, the others are written, and then `_raise_stale` names it.

    Args:
        app: The running `PtuiApp`.
        fixed: `(doc, key to (replaced, written))` per document.
        side: `0` to undo a step, `1` to redo it.

    Raises:
        StaleError: When some file changed on disk.
    """
    stale, wrote = [], []
    for doc, changed in fixed:
        values = {key: pair[side] for key, pair in changed.items()}
        try:
            safewrite.edit(doc, lambda data, values=values: _put_all(data, values))
        except safewrite.StaleError:
            stale.append(doc)
        except Exception as error:  # one document's failure must not stop the rest
            app.log_line(f"[red]{', '.join(values)} on {doc.get('ref', '')} failed:[/] {error}")
        else:
            wrote.append(doc)
    _resort(app)
    _raise_stale(app, stale, wrote)


def _raise_stale(app: Any, docs: list[Any], wrote: list[Any] | None = None) -> None:
    """End an undo or a redo that met these documents' `info.yaml` changed on disk.

    It rereads them, and raises `StaleError` naming them and counting the others it `wrote`.
    `undo.History` then keeps the step where it was, and `_not_done` says the key that tries it again:
    `_reread`'s "do it again" read as running the verb again.

    Args:
        app: The running `PtuiApp`.
        docs: The documents whose file changed; none means nothing is raised.
        wrote: The documents that were written, or `None`.

    Raises:
        StaleError: When `docs` is not empty.
    """
    for doc in docs:
        _resync(app, doc)
    if docs:
        refs = ", ".join(doc.get("ref") or "info.yaml" for doc in docs)
        error = safewrite.StaleError(f"{refs} changed on disk; reloaded from disk")
        left = {library.doc_id(doc) for doc in docs}
        error.done = len({library.doc_id(doc) for doc in wrote or []} - left)
        raise error


def tags_of(value: Any) -> list[str]:
    """Read a stored `tags` value as a list, whatever is in the file.

    The key is declared `tags:list` but a hand-written `tags: ml, cv` is legal YAML and happens.

    Args:
        value: The stored value.

    Returns:
        The tags, in order.
    """
    value = value or []
    return list(value) if isinstance(value, list) else library.typed("tags", str(value)) or []


@command("doc.tag", "add tags")
def doc_tag(app: Any, tags: str | None = None) -> None:
    """Add tags, keeping the ones already there. Batch-aware.

    Adding rather than replacing is the whole difference from `doc.set tags`:
    marking twenty documents and tagging them must not wipe what each already
    had. Order is preserved so the file diff stays small.

    Args:
        app: The running `PtuiApp`.
        tags: The tags to add, comma-separated, or `None` for the prompt.
    """
    if tags is None:
        app.open_prompt("tag", "tags to add")
        return
    wanted = library.typed("tags", tags) or []
    if not wanted:
        return

    def add(old: Any) -> list[str]:
        """Add the wanted tags after the ones stored.

        Args:
            old: The stored `tags` value.

        Returns:
            The stored tags, then the new ones.
        """
        current = tags_of(old)
        return [*current, *(tag for tag in wanted if tag not in current)]

    _set_field(app, "tags", add, f"+{', '.join(wanted)}")


@command("doc.untag", "remove tags")
def doc_untag(app: Any, tags: str | None = None) -> None:
    """Remove tags.

    A document left with none loses the key rather than keeping an empty list, which is what
    `library.typed` does for every other field.

    Args:
        app: The running `PtuiApp`.
        tags: The tags to remove, comma-separated, or `None` for the prompt.
    """
    if tags is None:
        here = ", ".join(tags_of(app.current.get("tags"))) if app.current else ""
        app.open_prompt("untag", f"tags to remove{f' — has: {here}' if here else ''}")
        return
    wanted = library.typed("tags", tags) or []
    if not wanted:
        return

    def drop(old: Any) -> list[str] | None:
        """Remove the unwanted tags.

        Args:
            old: The stored `tags` value.

        Returns:
            The tags left, or `None` when none are, which removes the key.
        """
        return [tag for tag in tags_of(old) if tag not in wanted] or None

    _set_field(app, "tags", drop, f"-{', '.join(wanted)}")


# ponytail: SPEC's three values, not a config key. `:doc.status submitted` still
# works — the field is a free string, the picker is just the common case.
STATUSES = ("unread", "reading", "read")


@command("doc.status", "reading status")
def doc_status(app: Any, value: str | None = None) -> None:
    """Set the reading status, or open the picker.

    Args:
        app: The running `PtuiApp`.
        value: The new status, empty to clear it; `None` for the picker.
    """
    if value is None:
        items = [ui.Item(label=status, value=status) for status in STATUSES]
        items.append(ui.Item(label="clear", value="", hint="remove the field"))
        current = app.current.get("reading_status") if app.current else None
        ui.pick(app, items, title="Reading status", current=current)(lambda status, _invert: doc_status(app, status))
        return
    _set_field(app, "reading_status", lambda _old: value or None, f"reading_status = {value or '-'}")


@command("doc.rating", "rating")
def doc_rating(app: Any, value: int | None = None) -> None:
    """Rate from 0 to 5, where 0 removes the key.

    Written as an int: papis does not declare a type for `rating`, so `library.typed` would keep it as
    text.

    Args:
        app: The running `PtuiApp`.
        value: The rating, clamped to 0..5; `None` for the picker.
    """
    if value is None:
        items = [ui.Item(label=str(rating) if rating else "0 — clear", value=rating) for rating in range(6)]
        current = app.current.get("rating") if app.current else None
        ui.pick(app, items, title="Rating", current=current)(lambda stars, _invert: doc_rating(app, stars))
        return
    stars = max(0, min(5, value))
    _set_field(app, "rating", lambda _old: stars or None, f"rating = {stars or '-'}")


# ── add ─────────────────────────────────────────────────────────────────────


@command("doc.add", "add document")
def doc_add(app: Any, source: str | None = None, uri: str | None = None) -> None:
    """Pick where the document comes from, confirm the metadata, then add it.

    With no `source` this lists every source papis can serve — a file, the inbox,
    a URL, and each registered importer with a row in `[add.sources]` — so a new
    papis plugin needs a config row, not a change here. `source` names one
    directly, which is how `i` reaches the inbox, and `uri` skips its prompt
    too: `:doc.add arxiv 2607.00687` is the paste-a-number path.

    Args:
        app: The running `PtuiApp`.
        source: The source's name, or a path; `None` lists every source.
        uri: What to import from that source, skipping its prompt; `None` prompts.
    """
    sources = app.config.get("add.sources")
    if source is None:
        _add_source_picker(app)
    elif uri is not None and (source in ("url", "bib") or source in sources):
        _import(app, source, uri)
    elif source == "url":
        app.open_prompt("import:url", "a publisher URL")
    elif source == "bib":
        app.open_prompt("import:bib", "path to a .bib")
    elif source in sources:
        app.open_prompt(f"import:{source}", sources[source]["example"])
    else:
        _add_from_file(app, source)


def _add_source_picker(app: Any) -> None:
    """List every source papis can serve.

    Args:
        app: The running `PtuiApp`.
    """
    inbox = app.config.as_path("files.inbox")
    items = [
        ui.Item(label="a file on disk…", value="", hint="path"),
        ui.Item(label=f"the inbox ({inbox})", value="inbox", hint="newest first"),
        ui.Item(label="a URL from any publisher…", value="url", hint=f"{len(fetch.downloaders())} downloaders"),
        ui.Item(label="a .bib file…", value="bib", hint="choose an entry"),
    ]
    sources = app.config.get("add.sources")
    items += [
        ui.Item(label=f"{sources[name]['label']}…", value=name, hint=name)
        for name in fetch.available(sources)
        if name != "bibtex"  # the .bib row above lists entries instead of taking the first
    ]
    ui.pick(app, items, title="Add from")(lambda source, _inverted: doc_add(app, source or "prompt"))


def _add_from_file(app: Any, source: str) -> None:
    """Add a file: prompted for, picked from the inbox, or named by `source`.

    Args:
        app: The running `PtuiApp`.
        source: `prompt`, `inbox`, or a path.
    """
    if source == "prompt":
        app.open_prompt("add", "path to a file")
    elif source == "inbox":
        items = _inbox(app)
        if items is not None:
            inbox = app.config.as_path("files.inbox")
            ui.pick(app, items, title=f"Add from {inbox}")(lambda path, _inverted: add_form(app, Path(path)))
    else:
        add_form(app, Path(source).expanduser())


def _inbox(app: Any) -> list[ui.Item] | None:
    """List the inbox, newest first: the file you just downloaded is the one you want.

    Args:
        app: The running `PtuiApp`.

    Returns:
        One picker row per file, or `None`, with a log line, when there is no inbox.
    """
    inbox = app.config.as_path("files.inbox")
    if inbox is None or not inbox.is_dir():
        app.log_line(f"[yellow]inbox not found:[/] {inbox}")
        return None
    entries = sorted(
        (path for path in inbox.iterdir() if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return [ui.Item(label=path.name, value=str(path), hint=_age(path)) for path in entries]


def _age(path: Path) -> str:
    """Show when a file was last modified.

    Args:
        path: The file.

    Returns:
        The date as `YYYY-MM-DD`.
    """
    from datetime import datetime

    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def _expand(data: dict[str, str]) -> dict[str, Any]:
    """Turn form fields into papis keys.

    `author` also becomes `author_list`, which is what naming schemes sort and format on.

    Args:
        data: Field name to its text.

    Returns:
        A new table with `author_list` added and `tags` split.
    """
    import papis.document

    expanded: dict[str, Any] = dict(data)
    if expanded.get("author"):
        expanded["author_list"] = papis.document.split_authors_name([expanded["author"]])
    if isinstance(expanded.get("tags"), str):
        expanded["tags"] = expanded["tags"].replace(",", " ").split()
    return expanded


#: Metadata keys the add form does not show but must not lose. A fetched record
#: carries the abstract, the arXiv id, the venue — dropping them because there is
#: no text box for them would make importing worse than typing it in by hand.
_KEEP_FETCHED = (
    "abstract",
    "author_list",
    "booktitle",
    "journal",
    "journaltitle",
    "publisher",
    "eprint",
    "eprinttype",
    "eprintclass",
    "url",
    "doc_url",
    "month",
    "pages",
    "volume",
    "number",
    "issn",
    "isbn",
    "language",
    "venue",
    "type",
)


def add_form(app: Any, path: Path | None, fetched: dict[str, Any] | None = None) -> None:
    """The one metadata form, for a file, a fetched record, or both.

    `path` may be None: an importer that found metadata but no PDF still deserves
    a document. `fetched` prefills the fields and its unshown keys ride along.

    Args:
        app: The running `PtuiApp`.
        path: The file to add, or `None` for a metadata-only document.
        fetched: The record to prefill from, or `None`.
    """
    if path is not None and not path.is_file():
        app.log_line(f"[red]no such file:[/] {path}")
        return

    rules = place.Rules.from_config(app.config)
    fetched = fetched or {}
    initial = {field: library.display(fetched.get(field, "")) for field in ui.ADD_FIELDS if fetched.get(field)}
    initial.setdefault("title", path.stem if path else "")

    def preview(data: dict[str, str]) -> str:
        """Where the file would end up. Pure: `place.target` touches nothing.

        Args:
            data: Field name to its text, as the form holds them.

        Returns:
            The line to show under the form.
        """
        import papis.document

        if path is None:
            return "no file — metadata only"
        doc = papis.document.from_data({**_expand(data), "files": [path.name]})
        rule = rules.first_match(path)
        if rule.operation == "in-place" or not rule.destination:
            return f"stays in the document folder ({rule.name})"
        try:
            destination = str(place.target(doc, path, rule, rules, default=""))
        except Exception as error:
            return f"destination not resolvable yet ({error})"
        # papis hands back the unformatted pattern when a key it needs is missing.
        if "{" in destination:
            return "fill in the fields above to see the destination"
        return f"-> {destination}"

    def confirm(data: dict[str, str] | None) -> None:
        """Add the document with the form's answer, unless it was cancelled.

        Args:
            data: Field name to its text, or `None` when cancelled.
        """
        if data is not None:
            extra = {key: value for key, value in fetched.items() if key in _KEEP_FETCHED and value}
            _add_document(app, path, data, extra)

    app.push_screen(ui.AddForm(path, initial, preview), ui.guarded("add", confirm))


def _duplicate(app: Any, data: dict[str, Any]) -> tuple[Any, str] | None:
    """The document already in the library that `data` describes, and why.

    papis's own test first — `locate_document_in_lib` over its
    `unique-document-keys` (DOI, ISBN, arXiv `eprint`, URL), the check `papis
    add` runs — then the same title, which papis does not try: in the same
    year first, then in any other (a preprint and its journal version).

    Args:
        app: The running `PtuiApp`.
        data: The new document's keys.

    Returns:
        `(document already there, why, as text)`, or `None` when nothing matches.
    """
    import papis.config
    import papis.database
    import papis.document
    import papis.utils

    name = app.config.get("general.library") or None
    candidate = papis.document.from_data(data)
    try:
        found = papis.utils.locate_document_in_lib(candidate, name)
    except IndexError:
        pass
    else:
        keys = papis.config.getlist("unique-document-keys") or []
        return found, next((key for key in keys if candidate.get(key) and found.get(key)), "a unique key")
    title, year = str(data.get("title") or "").casefold().strip(), str(data.get("year") or "")
    if not title:
        return None
    same = [
        doc
        for doc in papis.database.get(name).get_all_documents()
        if str(doc.get("title") or "").casefold().strip() == title
    ]
    same_year = [doc for doc in same if year and str(doc.get("year") or "") == year]
    if same_year:
        return same_year[0], "title and year"
    return (same[0], "title") if same else None


def _jump(app: Any, doc: Any) -> bool:
    """Put the cursor on `doc`, dropping a narrow that hides it.

    Args:
        app: The running `PtuiApp`.
        doc: The document to go to.

    Returns:
        False when the scope does not hold it: changing the scope is `s`, not a side effect.
    """
    key = library.doc_id(doc)
    if key not in {library.doc_id(candidate) for candidate in app.docs}:
        return False
    if key not in {library.doc_id(candidate) for candidate in app.rows}:
        app.marked_only = app.doctor_only = False
        app.refilter("")
    app.refresh_rows(keep=key)
    return True


def _add_document(
    app: Any, path: Path | None, data: dict[str, str], extra: dict[str, Any] | None = None, *, again: bool = False
) -> None:
    """Add a document, unless the library already holds it, which is asked first.

    Args:
        app: The running `PtuiApp`.
        path: The file to add, or `None` for a metadata-only document.
        data: Field name to its text, as the form returned them.
        extra: Fetched keys the form has no box for, or `None`.
        again: The answer "add it anyway" was given.
    """
    import papis.commands.add
    import papis.config

    form = data
    # The form wins over the fetched record: the user just looked at both.
    data = {**(extra or {}), **_expand(data)}
    if not again and (duplicate := _duplicate(app, data)) is not None:
        doc, why = duplicate
        what = f"{doc.get('ref') or library.display(doc.get('title', '?'))} ({doc.get('year', '?')})"
        items = [
            ui.Item(label=f"go to {what}", value="goto", hint=f"same {why}"),
            ui.Item(label="add it anyway", value="add"),
            ui.Item(label="cancel", value=""),
        ]

        def chose(answer: str, _invert: bool) -> None:
            """Act on the answer to the duplicate question.

            Args:
                answer: `add`, `goto`, or empty for cancel.
                _invert: The picker's `shift+enter` flag; unused.
            """
            if answer == "add":
                _add_document(app, path, form, extra, again=True)
            elif answer == "goto" and not _jump(app, doc):
                app.log_line(f"[yellow]{what} is outside the scope {app.scope_query or '*'}[/]")

        title = f"Already in the library? Same {why} as {what}"
        if why == "title":  # another year: say which one the library has
            title += f"; the library has it from {doc.get('year', '?')}, this one is from {data.get('year') or '?'}"
        ui.pick(app, items, title=title)(chose)
        return
    if papis.config.getboolean("time-stamp"):  # what `papis add` itself obeys
        data.setdefault(library.TIME_ADDED, library.stamp())
    # papis add asks about a duplicate itself, in a prompt_toolkit pager that
    # cannot run inside the TUI (asyncio.run under a running loop): the add
    # failed. The question is ptui's now, so papis's check is off for the call.
    unique = papis.config.get("unique-document-keys")
    papis.config.set("unique-document-keys", "[]")
    try:
        # ponytail: papis copies the source in; the original stays in the inbox
        # rather than being deleted behind the user's back. An empty path list is
        # a metadata-only document, which papis accepts.
        papis.commands.add.run(
            [str(path)] if path else [],
            data=dict(data),
            auto_doctor=app.config.get("add.auto_doctor", False),
            git=bool(app.config.papis("edit.use_git", "use-git")),
        )
    except Exception as error:
        app.log_line(f"[red]add failed:[/] {error}")
        return
    finally:
        papis.config.set("unique-document-keys", str(unique))

    reload(app)
    added = next((candidate for candidate in app.docs if candidate.get("title") == data.get("title")), None)
    if added is not None:
        app.refresh_rows(keep=library.doc_id(added))
        if path is not None:  # the new document only, never the marks; the add has no undo, so neither has this
            _relocate_run(app, [added], place.Rules.from_config(app.config), False, [])
    where = f" (source left at {path})" if path else " (no file)"
    app.log_line(f"added {data.get('title', path.name if path else '?')}{where}")


# ── export ──────────────────────────────────────────────────────────────────


def _yank(app: Any, text: str, what: str) -> None:
    """Copy text to the clipboard and say so.

    Args:
        app: The running `PtuiApp`.
        text: The text; empty is logged, not copied.
        what: What it is, for the log.
    """
    if not text:
        app.log_line(f"[yellow]nothing to yank for {what}[/]")
        return
    method = clip.copy(app, text)
    app.log_line(f"yanked {what} ({method})")


@command("export.citekey", "\\cite{ref}")
def export_citekey(app: Any) -> None:
    r"""The citekeys of the targets, per `export.citekey_format`.

    A document the format gives nothing for, what it makes of an empty one, `\cite{}` by default, is
    skipped and named, rather than yanked as an empty cite.

    Args:
        app: The running `PtuiApp`.
    """
    import papis.document
    import papis.format

    citekey_format = app.config.get("export.citekey_format", "{doc[ref]}")
    blank = papis.format.format(citekey_format, papis.document.from_data({}), default="")
    keys = []
    for doc in app.targets:
        # Verbatim stored values: LaTeX in a title is display-stripped, never yanked stripped.
        key = papis.format.format(citekey_format, doc, default="")
        if key and key != blank:
            keys.append(key)
        else:
            app.log_line(f"[yellow]no citekey for {library.display(doc.get('title', '?'))}; skipped[/]")
    _yank(app, " ".join(keys), f"{len(keys)} citekey(s)")


@command("export.path", "absolute path")
def export_path(app: Any) -> None:
    """Yank the main file of each target.

    Args:
        app: The running `PtuiApp`.
    """
    paths = [str(path) for doc in app.targets for path in files_of(app, doc)[:1]]
    _yank(app, "\n".join(paths), "path")


@command("export.url", "DOI/URL")
def export_url(app: Any) -> None:
    """Yank the URL or DOI link of each target.

    Args:
        app: The running `PtuiApp`.
    """
    urls = []
    for doc in app.targets:
        doi = doc.get("doi")
        urls.append(doc.get("url") or (f"https://doi.org/{doi}" if doi else ""))
    _yank(app, "\n".join(url for url in urls if url), "url")


@command("export.bibtex", "bibtex")
def export_bibtex(app: Any, target: str | None = None) -> None:
    """Export the targets as BibTeX.

    Args:
        app: The running `PtuiApp`.
        target: The file to write; `None` copies to the clipboard.
    """
    import papis.exporters.bibtex

    entries = [papis.exporters.bibtex.to_bibtex(doc) for doc in app.targets]
    text = "\n".join(entry for entry in entries if entry)
    if target:
        Path(target).expanduser().write_text(text)
        app.log_line(f"wrote {len(entries)} entries to {target}")
    else:
        _yank(app, text, f"{len(entries)} bibtex entrie(s)")


@command("export.as", "export as…")
def export_as(app: Any, fmt: str = "", target: str | None = None) -> None:
    """Export in any format papis exports (`papis export --list-exporters`).

    A new exporter plugin shows up here for free; to the clipboard, or to `target`.

    Args:
        app: The running `PtuiApp`.
        fmt: The exporter's name; empty opens a picker. Named by keys.toml's `args`, so public.
        target: The file to write; `None` copies to the clipboard.
    """
    import papis.commands.export
    from papis.exporters import get_available_exporters

    if not fmt:
        items = [ui.Item(label=name, value=name) for name in get_available_exporters()]
        ui.pick(app, items, title=f"Export {len(app.targets)} document(s) as")(
            lambda name, _inverted: export_as(app, name, target)
        )
        return
    docs = app.targets
    text = papis.commands.export.run(docs, fmt)  # logs its own failure and returns ""
    if docs and not text.strip():
        # Empty is papis's failure value (`csl` without citeproc-py): writing it
        # would empty the target file, and copying it would report success.
        where = target or "the clipboard"
        app.log_line(f"[red]export as {fmt} failed:[/] papis returned nothing; {where} left as it was")
        app.query_one("#log-pane").display = True
        return
    if target:
        Path(target).expanduser().write_text(text)
        app.log_line(f"wrote {len(docs)} document(s) as {fmt} to {target}")
    else:
        _yank(app, text, f"{len(docs)} document(s) as {fmt}")


# ── files ───────────────────────────────────────────────────────────────────


@command("files.relocate", "relocate + rename to scheme")
def files_relocate(app: Any, force: bool = False) -> None:
    """Run `place()` over every file of every target.

    Partial failure is normal: the clean ones go through, the rest are reported in the log. A
    batch confirms first, and the run is one undo step: the files and the `files` entries go
    back together.

    Args:
        app: The running `PtuiApp`.
        force: Take a suffixed name on a conflict instead of skipping.
    """
    docs = app.targets
    if not docs:
        return
    rules = place.Rules.from_config(app.config)
    _confirm_batch(app, len(docs), "relocate the files", lambda: _relocate_apply(app, docs, rules, force))


def _relocate_apply(app: Any, docs: list[Any], rules: place.Rules, force: bool) -> None:
    """Relocate, then push one undo step for the run.

    Redo runs it again on the same documents with the same rules, as a merge's redo does;
    `place()` is idempotent, so what it moved the first time lands where it did. A redo that
    meets a changed `info.yaml` keeps the step, and the next `ctrl+r` relocates only the
    documents it left: the documents a try relocated are never moved again, and one undo
    reverses every try, since all of them add to `landed`.

    Args:
        app: The running `PtuiApp`.
        docs: The documents to relocate.
        rules: The placement rules.
        force: Take a suffixed name on a conflict instead of skipping.
    """
    landed: list[tuple[Any, Any, list[tuple[str, place.PlaceResult]]]] = []
    _relocate_run(app, docs, rules, force, landed)
    if not landed:
        return
    left: list[Any] = []  # what a redo met changed on disk: all its next try relocates

    def redo() -> None:
        """Relocate again what the last try left.

        Raises:
            RuntimeError: When nothing moved and nothing is left to retry.
            StaleError: When an `info.yaml` changed on disk.
        """
        stale: list[Any] = []
        tried = len(landed)
        _relocate_run(app, left or docs, rules, force, landed, stale)
        left[:] = stale
        if not landed and not stale:
            raise RuntimeError("the relocate moved nothing this time; see the log")
        _raise_stale(app, stale, [doc for doc, _, _ in landed[tried:]])

    label = f"relocate the files of {len(landed)} document(s)"
    app.history.push(undo.Step(label, lambda: _relocate_back(app, landed), redo))


def _relocate_run(
    app: Any,
    docs: list[Any],
    rules: place.Rules,
    force: bool,
    landed: list[tuple[Any, Any, list[tuple[str, place.PlaceResult]]]],
    stale: list[Any] | None = None,
) -> None:
    """Do the relocate, adding to `landed` each document whose `files` it changed.

    `_relocate_back` reads `landed`. A document whose `info.yaml` changed on disk gets its files back,
    and goes to `_reread`, or into `stale` as in `_write_field`.

    Args:
        app: The running `PtuiApp`.
        docs: The documents to relocate.
        rules: The placement rules.
        force: Take a suffixed name on a conflict instead of skipping.
        landed: Receives `(doc, old files value, [(entry, place result)])` per changed document.
        stale: Receives the documents whose file changed; `None` rereads them at once.
    """
    tally: dict[str, int] = {}
    for doc in docs:
        entries = place.entries(doc)
        results = []
        for entry in entries:
            # One entry's surprise must not strand the ones already moved: they
            # are written below, and this one keeps its old path.
            try:
                results.append(place.place(doc, entry, rules, force=force, previous=entry))
            except Exception as error:
                results.append(place.PlaceResult("error", place.resolve(doc, entry), "", message=str(error)))
        for result in results:
            tally[result.status] = tally.get(result.status, 0) + 1
            if result.status not in ("ok", "already", "unmanaged"):
                app.log_line(f"[yellow]{result.status}[/] {result.source.name}: {result.message}")

        moved = {
            stored: outcome.entry
            for stored, outcome in zip(entries, results, strict=True)
            if outcome.entry and outcome.status in ("ok", "already")
        }
        if all(stored == new for stored, new in moved.items()):
            continue
        # `files` as the file holds it by now, so an entry a script added stays
        wrote = _write_field(
            app,
            [(doc, lambda old, moved=moved: [moved.get(stored, stored) for stored in _entries(old)])],
            "files",
            stale,
        )
        if wrote:  # for undo: what the write replaced, and each moved file with the entry it had
            landed.append(
                (
                    doc,
                    wrote[0][1],
                    [(stored, outcome) for stored, outcome in zip(entries, results, strict=True) if outcome.moved],
                )
            )
        else:  # file first, info.yaml second: a write that did not land takes the files back
            for result in results:
                place.rollback(result)
            app.log_line(f"[red]rolled back {doc.get('ref', '')}[/]")

    app.refresh_rows()
    summary = ", ".join(f"{count} {status}" for status, count in sorted(tally.items()))
    app.log_line(f"relocate: {summary or 'nothing to do'}")
    if tally.get("conflict") or tally.get("error"):
        app.query_one("#log-pane").display = True


def _relocate_back(app: Any, landed: list[tuple[Any, Any, list[tuple[str, place.PlaceResult]]]]) -> None:
    """Undo the relocate runs recorded in `landed`, and keep in it only what is left.

    Args:
        app: The running `PtuiApp`.
        landed: The list `_relocate_run` filled; narrowed in place to what is left to undo.

    Raises:
        StaleError: When an `info.yaml` changed on disk.
    """
    stale: list[Any] = []
    wrote = []
    for doc, old, placed in landed:
        # `files` first, as a merge's undo does: a write refused as stale
        # leaves this document's files where its entries still say they are
        if not _write_field(app, [(doc, old)], "files", stale):
            continue
        wrote.append(doc)
        stuck = {}
        for entry, result in placed:
            try:
                place.rollback(result)
            except OSError as error:
                app.log_line(f"[red]{result.destination} not moved back to {result.source}:[/] {error}")
                stuck[entry] = result.entry
        if stuck:  # the entry names where the file still is, not the path it could not go back to
            again = [(doc, lambda files, stuck=stuck: [stuck.get(stored, stored) for stored in _entries(files)])]
            _write_field(app, again, "files", stale)
    app.refresh_rows()
    # all a retry has left to undo: a file moved back once must not move again
    landed[:] = [item for item in landed if any(item[0] is doc for doc in stale)]
    _raise_stale(app, stale, wrote)


def _file_entry(app: Any, index: int | None) -> tuple[Any, list[str], int] | None:
    """Find the entry a `files` verb acts on.

    Args:
        app: The running `PtuiApp`.
        index: The entry's position; `None` means the files pane's cursor.

    Returns:
        `(document, copy of its files, position)`, or `None`, with a log line, when there is no such
        entry.
    """
    doc = app.current
    entries = place.entries(doc) if doc is not None else []
    if not entries:
        app.log_line("[yellow]no files attached[/]")
        return None
    at = app.file_index if index is None else index
    if not 0 <= at < len(entries):
        app.log_line(f"[yellow]no file {at}; this document has {len(entries)}[/]")
        return None
    return doc, entries, at


@command("files.detach", "remove from the list, keep the file")
def files_detach(app: Any, index: int | None = None) -> None:
    """Drop one `files` entry and leave the file where it is.

    That is the half `papis rm --file` does not offer, since it deletes the file too. One undo step.

    Args:
        app: The running `PtuiApp`.
        index: The entry's position; `None` means the files pane's cursor.
    """
    found = _file_entry(app, index)
    if found is None:
        return
    doc, entries, at = found
    gone = entries[at]
    _fields_apply(app, doc, {"files": lambda old: _without(old, gone)})
    app.log_line(f"detached {gone}; the file is still at {place.resolve(doc, gone)}")


def _entries(stored: Any) -> list[str]:
    """Read a `files` value inside a write, entry by entry.

    `place.entries` for the value itself, so a hand-written `files: paper.pdf` is one entry.

    Args:
        stored: The value as the file holds it.

    Returns:
        The entries as a new list.
    """
    return place.entries({"files": stored})


def _without(stored: Any, entry: str) -> list[str] | None:
    """`files` as the file holds it, less the first `entry`; `None` for none left.

    Args:
        stored: The value as the file holds it.
        entry: The entry to take out.

    Returns:
        The entries left, or `None` when none are, which removes the key.
    """
    entries = _entries(stored)
    if entry in entries:
        entries.remove(entry)
    return entries or None


@command("files.reorder", "move a file up or down")
def files_reorder(app: Any, index: int | None = None, to: int = 1) -> None:
    """Move one entry `to` places, -1 being up.

    The first entry is "the" file: what `o` opens and what the naming rules call the main one.

    Args:
        app: The running `PtuiApp`.
        index: The entry's position; `None` means the files pane's cursor.
        to: Places to move; negative moves up.
    """
    found = _file_entry(app, index)
    if found is None:
        return
    doc, entries, at = found
    target = max(0, min(len(entries) - 1, at + to))
    if target == at:
        return
    entry = entries[at]

    def moved(old: Any) -> list[str]:
        """The move, made on `files` as the file holds it.

        Args:
            old: The `files` value as the file holds it.

        Returns:
            The entries in their new order.
        """
        old = _entries(old)
        if entry in old:
            here = old.index(entry)
            old.insert(max(0, min(len(old) - 1, here + to)), old.pop(here))
        return old

    _fields_apply(app, doc, {"files": moved})
    now = place.entries(doc)
    app.file_index = now.index(entry) if entry in now else target  # the cursor stays on the file it moved
    app.refresh_files()


@command("files.repoint", "point an entry at another file")
def files_repoint(app: Any, index: int | None = None, path: str | None = None) -> None:
    """Replace one `files` entry: the fix for a path that broke.

    With no `path`, a picker over the document folder's files that no entry points at yet (the usual
    culprit is a rename), and "a path…" for anything else. The new entry keeps the old one's style:
    relative stays relative where it can.

    Args:
        app: The running `PtuiApp`.
        index: The entry's position; `None` means the files pane's cursor.
        path: The file to point at; `None` opens the picker.
    """
    found = _file_entry(app, index)
    if found is None:
        return
    doc, entries, at = found
    if path is not None:
        target = place.normal(Path(path).expanduser())  # the path as typed: a link stays a link
        if not target.is_file():
            app.log_line(f"[red]no such file:[/] {target}")
            return
        before, after = entries[at], place._entry(target, doc, "keep", entries[at])
        _fields_apply(
            app, doc, {"files": lambda old: [after if stored == before else stored for stored in _entries(old)]}
        )
        return
    folder = Path(doc.get_main_folder() or ".")
    held = {place.resolve(doc, entry) for entry in entries}
    loose = sorted(
        loose_file
        for loose_file in folder.iterdir()
        if loose_file.is_file() and loose_file.name != "info.yaml" and loose_file not in held
    )
    items = [
        ui.Item(label="a path…", value="", hint="type it"),
        *(ui.Item(label=loose_file.name, value=str(loose_file)) for loose_file in loose),
    ]
    ui.pick(app, items, title=f"{entries[at]} -> ?")(
        lambda chosen, _inverted: (
            files_repoint(app, at, chosen) if chosen else app.open_prompt(f"repoint:{at}", f"path for {entries[at]}")
        )
    )


@command("files.rename", "rename a file to papis's scheme")
def files_rename(app: Any, index: int | None = None) -> None:
    """Rename one file, where it is, to what papis's `add-file-name` makes of this document.

    That is the name `papis add` would have given it, with papis's own suffixes against a clash. Never
    clobbers; file first, `info.yaml` second, and the file goes back if the write fails. One undo step.

    Never clobbers; file first, `info.yaml` second, and the file goes back if the write fails.
    One undo step.

    With no `add-file-name` papis only normalises the old name, which would turn
    `paper_supp.pdf` into `paper-supp.pdf` and out of `files.kind_patterns`; so
    then it says so and renames nothing.

    Args:
        app: The running `PtuiApp`.
        index: The entry's position; `None` means the files pane's cursor.
    """
    import itertools

    import papis.config
    import papis.paths

    found = _file_entry(app, index)
    if found is None:
        return
    doc, entries, at = found
    if not papis.config.getstring("add-file-name"):
        app.log_line("[yellow]papis's add-file-name is not set, so there is no scheme to rename to[/]")
        return
    old_entry = entries[at]
    source = place.resolve(doc, old_entry)
    if not source.is_file():
        app.log_line(f"[red]missing file:[/] {source} — p repoints it")
        return
    # papis's scheme names the paper; a supplement renamed by it becomes a
    # second main file, and its `_supp` — the only record of what it is — is gone
    for kind, patterns in app.config.get("files.kind_patterns", {}).items():
        if any(fnmatch(source.name, pattern) for pattern in patterns):
            app.log_line(f"[yellow]{source.name} is {kind} by its name; the scheme would make it the paper[/]")
            return
    for suffix in ("", *itertools.islice(papis.paths.unique_suffixes(), 25)):
        destination = source.with_name(papis.paths.get_document_file_name(doc, source, suffix))
        if destination == source:
            app.log_line(f"{source.name} already follows the scheme")
            return
        if place._existing(destination) is None:
            break
    else:
        app.log_line(f"[red]no free name for {source.name}[/]")
        return
    new_entry = place._entry(destination, doc, "keep", old_entry)

    def move(origin: Path, target: Path, before: str, after: str) -> None:
        """Rename a file and rewrite its entry, the file going back if the write fails.

        Args:
            origin: The file as it is now.
            target: The name to give it.
            before: The entry as `files` holds it now.
            after: The entry to write instead.

        Raises:
            FileExistsError: When `target` already exists.
            StaleError: When `info.yaml` changed on disk.
        """
        place._reserve(origin, target)  # raises rather than clobber
        origin.unlink()

        def repoint(data: Any) -> None:  # `files` as the file holds it, not as it was loaded
            """Rewrite the entry in `files` as the file holds it, not as it was loaded.

            Args:
                data: The ruamel map of `info.yaml`.
            """
            old = _entries(safewrite.plain(data.get("files")))
            safewrite.put(data, "files", [after if entry == before else entry for entry in old])

        try:
            safewrite.edit(doc, repoint)
        except Exception:
            place._reserve(target, origin)
            target.unlink()
            raise
        app.refresh_rows()

    move(source, destination, old_entry, new_entry)
    app.history.push(
        undo.Step(
            f"rename {source.name}",
            lambda: move(destination, source, new_entry, old_entry),
            lambda: move(source, destination, old_entry, new_entry),
        )
    )
    app.log_line(f"renamed {source.name} -> {destination.name}")


@command("files.normalize", "normalize path styles")
def files_normalize(app: Any) -> None:
    """Rewrite every `files` entry of the targets in the style its placement rule asks for.

    Under `keep`, that is relative inside the document folder and absolute outside it. Entries that
    point
    at the same file twice are dropped. Explicit only: nothing else in ptui changes a path's style. A
    batch confirms first, counting the documents that change, and the run is one undo step.

    Args:
        app: The running `PtuiApp`.
    """
    rules = place.Rules.from_config(app.config)

    def normalized(doc: Any, entries: Any) -> list[str]:
        """Work out a document's entries in their rule's style.

        Args:
            doc: The document they belong to.
            entries: The `files` value as stored.

        Returns:
            The new entries, without repeats.
        """
        folder = place.normal(Path(doc.get_main_folder() or "."))
        seen, new = set(), []
        for entry in _entries(entries):
            # Never through a link: a linked PDF stays the link, and a library
            # reached through one keeps its own spelling of the folder.
            path = place.normal(place.resolve(doc, entry))
            if path in seen:
                continue
            seen.add(path)
            style = rules.first_match(path).path_style
            if style == "keep":
                style = "relative-doc" if folder in path.parents else "absolute"
            new.append(place._entry(path, doc, style, None))
        return new

    planned = []
    for doc in app.targets:
        if normalized(doc, doc.get("files")) != place.entries(doc):
            # written from `files` as the file holds it then: an entry a script added stays
            planned.append((doc, lambda old, doc=doc: normalized(doc, old)))
    if not planned:
        app.log_line("every path is already in its style")
        return
    _confirm_batch(app, len(planned), "normalize paths", lambda: _set_apply(app, planned, "files"))


@command("files.attach", "attach file")
def files_attach(app: Any, path: str | None = None) -> None:
    """Add a file or a URL to the current document — `papis addto`, verbatim.

    papis copies it into the document folder under its own name and leaves the
    source alone; no placement rules run. Marks are ignored: one file belongs to
    one document. With no `path` this picks from the inbox, or asks for one.

    Args:
        app: The running `PtuiApp`.
        path: A file or a URL; `None` picks from the inbox or asks.
    """
    import papis.paths

    doc = app.current
    if doc is None:
        return
    if path is None:
        items = [ui.Item(label="a file on disk or a URL…", value="", hint="path"), *(_inbox(app) or [])]
        ui.pick(app, items, title=f"Attach to {doc.get('ref', '')}")(
            lambda source, _inverted: (
                files_attach(app, source) if source else app.open_prompt("attach", "path or URL to attach")
            )
        )
    elif papis.paths.is_remote_file(path):
        app.log_line(f"[dim]downloading {path}…[/]")
        app.run_worker(lambda: _download(app, doc, path), thread=True, group="attach", exit_on_error=False)
    else:
        try:
            source = Path(path).expanduser()
        except RuntimeError as error:  # `~nosuchuser/…`: there is no home to expand
            app.log_line(f"[red]cannot attach[/] {path}: {error}")
            return
        _attach(app, doc, source)


def _download(app: Any, doc: Any, url: str) -> None:
    """Thread worker: a download takes seconds and would freeze the app.

    Args:
        app: The running `PtuiApp`.
        doc: The document to attach to.
        url: The file's URL.
    """
    import papis.paths

    try:
        local = papis.paths.download_remote_files([url])[0]
    except Exception as error:
        app.call_from_thread(app.log_line, f"[red]download failed:[/] {error}")
        return
    if local is None:  # papis logged why
        app.call_from_thread(app.log_line, f"[red]download failed:[/] {url}")
        return
    app.call_from_thread(_attach, app, doc, Path(local), downloaded=True)


def _attach(app: Any, doc: Any, source: Path, *, downloaded: bool = False) -> None:
    """Attach one local file through `papis addto`.

    Args:
        app: The running `PtuiApp`.
        doc: The document to attach to; reloaded from disk.
        source: The file to attach.
        downloaded: The file is a temporary download: remove it afterwards.
    """
    import papis.commands.addto

    ref = doc.get("ref", "")
    before: list[str] = []
    try:
        if not source.is_file():
            app.log_line(f"[red]no such file:[/] {source}")
            return
        # addto writes this in-memory copy back whole, past `safewrite`: start
        # from what is on disk, or an edit made since loading is lost.
        doc.load()
        if (held := place.holding(doc, source)) is not None:
            app.log_line(f"[yellow]{ref} already has {source.name} (as {held.name})[/]")
            return
        before = place.entries(doc)
        papis.commands.addto.run(doc, [str(source)], git=bool(app.config.papis("edit.use_git", "use-git")))
    except Exception as error:
        app.log_line(f"[red]attach failed:[/] {error}")
        return
    finally:
        doc.load()  # whatever addto left in memory, show what is on disk
        if downloaded:
            source.unlink(missing_ok=True)

    added = [entry for entry in place.entries(doc) if entry not in before]
    app.refresh_rows()
    if added:
        app.log_line(f"attached {', '.join(added)} to {ref}")
    else:
        app.log_line(f"[yellow]nothing attached to {ref}; see the papis log above[/]")


# ── app ─────────────────────────────────────────────────────────────────────


@command("app.escape", "cancel")
def app_escape(app: Any) -> None:
    """Resolve per `escape_chain`. Never clears marks — only `m c` does.

    Args:
        app: The running `PtuiApp`.
    """
    for step in app.keymap.option("escape_chain", ["modal", "narrow"]):
        if step == "modal" and app.prompt_kind:
            app.close_prompt()
            return
        if step == "visual" and app.visual is not None:
            _visual_end(app)
            return
        if step == "narrow" and (app.narrow_query or app.doctor_only):
            app.doctor_only = False  # the doctor view is a narrow, so escape drops it too
            app.refilter("")
            return
    app.pending = ()


@command("help.show", "help")
def help_show(app: Any) -> None:
    """Discoverability layer 3: the *effective* keymap of the current mode.

    Generated from the keymap, so user overrides show up and a static blob can
    never drift. Browsing is safe — enter closes, it does not run the command;
    running things by name is the `:` command line's job.

    Args:
        app: The running `PtuiApp`.
    """
    items = []
    for binding in sorted(
        app.keymap.modes.get(app.mode, {}).values(),
        key=lambda binding_entry: (binding_entry.chord[0].casefold(), binding_entry.keys),
    ):
        # as keys.toml spells them: `current=true` says what `True` alone does not
        args = " ".join(
            f"{key}={str(value).lower() if isinstance(value, bool) else value}" for key, value in binding.args.items()
        )
        note = "" if binding.command in REGISTRY else "  (not implemented)"
        label = f"{binding.keys:<10} {binding.description or binding.command}{f' — {args}' if args else ''}"
        hint = "" if binding.description in ("", binding.command) else binding.command  # no echo of the label
        items.append(ui.Item(label=label + note, value=binding.command, hint=hint))
    if app.mode != "list":
        # Guaranteed by the dispatcher, so it is in no mode's table.
        items = [ui.Item(label=f"{'escape':<10} back to the list", value=""), *items]
    app.push_screen(ui.SelectList(items, title=f"Keys — {app.mode} mode"))


@command("cmdline.open", "run a command by name")
def cmdline_open(app: Any) -> None:
    """Discoverability layer 4: fuzzy completion over command names.

    Each has its binding beside it: the layer that teaches the keymap.

    It lists the *registry*, not the keymap: unlike `help.show` this runs what
    you pick, so a command nobody has bound is still reachable, and one that is
    bound teaches its keys on the way past. The keys come from the current mode,
    falling back to the list mode's table so a pane with no bindings of its own
    still shows where the command lives.

    Args:
        app: The running `PtuiApp`.
    """
    show_keys = app.keymap.option("show_keys_in_cmdline", True)

    def keys(name: str) -> str:
        """Find the keys of a command, for its row.

        Args:
            name: Dotted command name.

        Returns:
            The keys, empty when unbound or switched off.
        """
        if not show_keys:
            return ""
        return app.keymap.for_command(app.mode, name) or app.keymap.for_command("list", name) or ""

    items = [
        ui.Item(
            label=f"{name:<20} {registered.description}",
            value=name,
            hint=keys(name),
            haystack=commands.signature(name),
        )
        for name, registered in sorted(REGISTRY.items())
    ]
    ui.pick(app, items, title="Run command")(lambda name, _invert: cmdline_args(app, name))


def cmdline_args(app: Any, name: str) -> None:
    """Ask for the arguments, or run straight away when there are none.

    Arguments are what the command line is *for*: everything else already has a
    key. A command with parameters therefore always gets the prompt, optional
    ones included — `enter` on an empty line keeps every default, which is what
    a binding with no `args` does.

    Args:
        app: The running `PtuiApp`.
        name: Dotted command name.
    """
    if not commands.params(name):
        app.run_command(name)
        return
    app.open_prompt(f"cmdline:{name}", f"{name} {commands.signature(name)}")


def cmdline_run(app: Any, name: str, text: str) -> None:
    """Run a command with the arguments typed for it.

    Args:
        app: The running `PtuiApp`.
        name: Dotted command name.
        text: The argument line as typed.
    """
    try:
        args = commands.parse_args(name, text)
    except ValueError as error:  # too many arguments, an unclosed quote, a bad number
        app.log_line(f"[red]{error}[/]")
        return
    app.log_line(f"[dim]:{name}{' ' + text if text.strip() else ''}[/]")
    app.run_command(name, args)


@command("theme.picker", "theme…")
def theme_picker(app: Any, name: str | None = None) -> None:
    """Switch palette, live.

    The highlighted theme is shown as you move, and `escape` puts back the one that was there. The
    themes
    are Textual's own, 21 of them, light and dark, so there is no palette file here to rot against a
    release.

    The choice lasts the session: `[ui] theme` in `config.toml` is what makes it
    stick, and the log says so rather than writing to the user's config behind
    their back.

    Args:
        app: The running `PtuiApp`.
        name: A theme to apply at once; `None` opens the picker.
    """
    if name is not None:
        app.apply_theme(name)
        return
    items = [
        ui.Item(label=theme, value=theme, hint="light" if not app.available_themes[theme].dark else "")
        for theme in sorted(app.available_themes)
    ]

    def apply(theme: str, _invert: bool) -> None:
        """Apply the picked theme and say how to keep it.

        Args:
            theme: The theme's name.
            _invert: The picker's `shift+enter` flag; unused.
        """
        app.apply_theme(theme)
        app.refresh_rows()  # the marked-row colour is a Rich style, not CSS
        app.log_line(f'theme: {theme} — keep it with [bold]theme = "{theme}"[/] under [ui]')

    # a preview leaves the list alone: repainting it is 0.3 s at 5000 documents, per theme passed
    ui.pick(app, items, title="Theme", current=app.theme, preview=app.apply_theme)(apply)


@command("keymap.check", "check keymap conflicts")
def keymap_check(app: Any) -> None:
    """Log every keymap conflict and unknown command.

    Args:
        app: The running `PtuiApp`.
    """
    problems = [*app.keymap.conflicts(), *app.keymap.unknown_commands]
    for problem in problems:
        app.log_line(f"[yellow]{problem}[/]")
    app.log_line(f"keymap: {len(problems)} problem(s)")
    app.query_one("#log-pane").display = True


STARTUP_ONLY = frozenset(
    {
        "log.level",
        "log.file",
        "log.max_entries",
        "ui.mouse",
        "undo.stack_size",
        "general.startup_query",
        "doctor.scan_on_startup",
    }
)
"""Config keys read once while ptui starts: `config.set` on one would change nothing."""


@command("config.set", "set a config value for this session")
def config_set(app: Any, key: str, value: str) -> None:
    """Try a setting without editing `config.toml` and restarting.

    Session only: nothing is written anywhere.

    The value is read as TOML — `2`, `true`, `["a", "b"]` — and falls back to
    the bare text, so `:config.set ui.theme nord` needs no quotes. It is checked
    the way `config.toml` is, so a value that cannot work is replaced and said.

    Args:
        app: The running `PtuiApp`.
        key: Dotted key of an existing setting.
        value: The value as typed.
    """
    import tomllib

    section, _, leaf = key.rpartition(".")
    table = app.config.get(section) if section else None
    if not isinstance(table, dict) or leaf not in table:
        app.log_line(f"[red]no config key[/] {key}")  # a new key would be read by nothing
        return
    if key in STARTUP_ONLY:
        app.log_line(f"[yellow]{key} takes effect on restart; set it in config.toml[/]")
        return
    if key == "general.library":  # papis has a current library too; switching moves both
        lib_switch(app, value)
        return
    try:
        new = tomllib.loads(f"value = {value}")["value"]
    except tomllib.TOMLDecodeError:
        new = value
    old, table[leaf] = table[leaf], new
    for problem in config.repair(app.config.data):
        app.log_line(f"[yellow]{problem}[/]")
    if key == "undo.strategy" and (why := undo.require_git(app.config, library_dir(app))):
        app.log_line(f"[yellow]{why}[/]")
    app.apply_config()
    app.log_line(f"{key} = {table[leaf]!r} until you quit (was {old!r}) — config.toml is where it stays")


@command("app.config_check", "check config")
def config_check(app: Any) -> None:
    """Log every unknown key and invalid value of the config.

    Args:
        app: The running `PtuiApp`.
    """
    for key in app.config.unknown:
        app.log_line(f"[yellow]unknown config key:[/] {key}")
    for problem in app.config.invalid:
        app.log_line(f"[yellow]invalid:[/] {problem}")
    app.log_line(
        f"config: {len(app.config.unknown)} unknown key(s), {len(app.config.invalid)} invalid value(s)"
        f" in {app.config.path or 'defaults'}"
    )
    app.query_one("#log-pane").display = True


# ── merge ───────────────────────────────────────────────────────────────────


@command("doc.merge", "merge marked documents")
def doc_merge(app: Any) -> None:
    """Fold the marked documents into one.

    The `ref` you keep is the document you keep: it holds the folder and the
    `papis_id`, and the others' folders go to the trash. Keys only the others had
    are filled in silently; a key two records genuinely disagree on is asked
    about, one question at a time.

    Args:
        app: The running `PtuiApp`.
    """
    docs = [member for member in app.docs if library.doc_id(member) in app.marks]
    if len(docs) < 2:
        app.log_line("[yellow]merge needs at least two marked documents[/]")
        return

    choices = merge.survivor_choices(docs)
    if len(choices) == 1:
        _merge_conflicts(app, choices[0][1], [member for member in docs if member is not choices[0][1]])
        return

    items = [
        ui.Item(
            label=ref,
            value=index,
            hint=f"{doc.get('type', '')} {len(place.entries(doc))} file(s)".strip(),
            haystack=library.display(doc.get("title", "")),
        )
        for index, (ref, doc) in enumerate(choices)
    ]

    def picked(index: int, _invert: bool) -> None:
        """Merge into the document the picked `ref` belongs to.

        Args:
            index: Position in the survivor choices.
            _invert: The picker's `shift+enter` flag; unused.
        """
        survivor = choices[index][1]
        _merge_conflicts(app, survivor, [member for member in docs if member is not survivor])

    ui.pick(app, items, title=f"Merge {len(docs)} documents — which ref survives?")(picked)


def _merge_conflicts(app: Any, survivor: Any, others: list[Any]) -> None:
    """Plan a merge, ask about its clashes, then apply it.

    Args:
        app: The running `PtuiApp`.
        survivor: The document that survives.
        others: The documents folded into it.
    """
    plan = merge.plan(survivor, others)
    names = [str(doc.get("ref") or doc.get("title", "?")) for doc in [survivor, *others]]
    _ask_clashes(app, plan, names, lambda resolved: _merge_apply(app, plan, resolved))


def _ask_clashes(app: Any, plan: Any, names: list[str], done: Callable[[dict[str, Any]], None]) -> None:
    """Ask about each genuine clash, then hand the answers to `done`.

    Recursive: one modal per key. `names` label the records, survivor first.

    Args:
        app: The running `PtuiApp`.
        plan: The `merge.Plan`, whose `clashes` are asked about.
        names: Labels of the records, survivor first.
        done: Receives key to chosen value, once everything is answered.
    """
    import papis.document

    group = [plan.survivor, *plan.others]
    resolved: dict[str, Any] = {}

    def show(key: str, value: Any) -> str:
        """Render a clash's value for the picker.

        Args:
            key: The clashing key.
            value: One record's value.

        Returns:
            The value as text.
        """
        if key == "author_list":  # a list of dicts; the author string is what anyone reads
            return papis.document.author_list_to_author({"author_list": value})
        return library.display(value)

    def ask(remaining: list[str]) -> None:
        """Ask about the next clash, or finish.

        Args:
            remaining: The keys still to ask about.
        """
        if not remaining:
            done(resolved)
            return
        key, rest = remaining[0], remaining[1:]
        values = plan.clashes[key]
        items = [
            ui.Item(label=show(key, value), value=("value", index), hint=key) for index, value in enumerate(values)
        ]
        # the shortcut: stop being asked and take this record's side throughout
        items += [
            ui.Item(label=f"keep everything else from {name}", value=("all", position), hint="resolves the rest")
            for position, name in enumerate(names)
        ]

        def chose(choice: tuple[str, int], _invert: bool) -> None:
            """Record an answer: one value, or a whole record's side for the rest.

            Args:
                choice: `("value", index)` into the key's values, or `("all", index)` into the records.
                _invert: The picker's `shift+enter` flag; unused.
            """
            kind, index = choice
            if kind == "value":
                resolved[key] = values[index]
                ask(rest)
                return
            winner = group[index]
            for pending in [key, *rest]:
                if winner.get(pending) not in (None, "", [], {}):
                    resolved[pending] = winner.get(pending)
            done(resolved)

        ui.pick(app, items, title=f"{key} — {len(remaining)} left")(chose)

    ask(plan.questions)


@command("doc.refresh", "update from a source")
def doc_refresh(app: Any, source: str = "") -> None:
    """Fetch the document again and fold in what is new.

    That is `papis update --auto` without its FIXME: papis overwrites every key the source has, so a
    title you fixed by hand reverts. Here a key the document lacks is filled, and each real disagreement
    is one question, one write and one undo step for all of it. The sources come from the document
    itself: its DOI, its arXiv id, its title.

    Args:
        app: The running `PtuiApp`.
        source: The importer's name; empty offers every one that applies.
    """
    doc = app.current
    if doc is None:
        return
    importers = [importer_option for importer_option in fetch.refetchers(doc) if source in ("", importer_option.name)]
    if not importers:
        app.log_line(f"[yellow]nothing to refetch {doc.get('ref', '')} from{f' with {source}' if source else ''}[/]")
        return
    if len(importers) == 1:
        _refresh_fetch(app, doc, importers[0])
        return
    hints = {"crossref": "a title search: pick the paper"}
    items = [
        ui.Item(label=importer_option.name, value=index, hint=hints.get(importer_option.name, str(importer_option.uri)))
        for index, importer_option in enumerate(importers)
    ]
    ui.pick(app, items, title=f"Update {doc.get('ref', '')} from")(
        lambda index, _inverted: _refresh_fetch(app, doc, importers[index])
    )


def _refresh_fetch(app: Any, doc: Any, importer: Any) -> None:
    """Fetch off the UI thread, then continue with what came back.

    Args:
        app: The running `PtuiApp`.
        doc: The document to refresh.
        importer: The papis importer to run.
    """
    app.log_line(f"[dim]fetching {doc.get('ref', '')} from {importer.name}…[/]")

    def job() -> None:
        """Fetch in the worker thread, handing the result back to the app's own."""
        try:
            hits = fetch.refetch(importer)
        except Exception as error:
            app.call_from_thread(app.log_line, f"[red]{importer.name} failed:[/] {error}")
            return
        app.call_from_thread(_refresh_found, app, doc, importer.name, hits)

    app.run_worker(job, thread=True, group="import", exit_on_error=False)


def _refresh_found(app: Any, doc: Any, source: str, hits: list[dict[str, Any]]) -> None:
    """Fold in what a fetch found, asking first when it was a title search.

    An id names its record. A title search finds *a* paper, whose gaps would be filled without a
    question, so the first question is which one, if any, is this paper.

    Args:
        app: The running `PtuiApp`.
        doc: The document to refresh.
        source: The importer's name.
        hits: The records found, best first.
    """
    if source != "crossref":
        _refresh_merge(app, doc, source, hits[0])
        return
    if len(hits) == 1:
        summary = _hit_summary(hits[0])
        found = library.display(hits[0].get("title", "?")) + (f" ({summary})" if summary else "")
        title = f"Crossref found: {found} — is this the paper?"
        items = [
            ui.Item(label="yes, update from it", value=0),
            ui.Item(label="no, leave it as it is", value=NONE_OF_THESE),
        ]
    else:
        title = f"Crossref found {len(hits)} — which is {doc.get('ref', '')}?"
        items = _hit_items(hits)

    def answer(index: int, _invert: bool) -> None:
        """Continue with the hit the user accepted, if any.

        Args:
            index: The hit's position, or `NONE_OF_THESE`.
            _invert: The picker's `shift+enter` flag; unused.
        """
        if index == NONE_OF_THESE:
            app.log_line(f"{source}: not the paper; {doc.get('ref', '')} left as it was")
        else:
            _refresh_merge(app, doc, source, hits[index])

    ui.pick(app, items, title=title)(answer)


def _refresh_merge(app: Any, doc: Any, source: str, data: dict[str, Any]) -> None:
    """Plan the fetched record as a merge into the document, and ask about its clashes.

    Args:
        app: The running `PtuiApp`.
        doc: The document to refresh; reloaded from disk.
        source: The importer's name.
        data: The fetched record.
    """
    doc.load()  # a value the file gained since loading is a question, not a gap
    plan = merge.plan(doc, [data])
    if not plan.gaps and not plan.clashes:
        app.log_line(f"{source}: nothing new for {doc.get('ref', '')}")
        return

    def gap(value: Any) -> Any:  # still one when written, or what filled it meanwhile stays
        """Fill a gap only if it is still one when written.

        Args:
            value: The fetched value.

        Returns:
            A function of the stored value giving what to write.
        """
        return lambda old: value if old in merge.BLANK else old

    def apply(resolved: dict[str, Any]) -> None:
        """Write what changed, as one undo step.

        Args:
            resolved: Key to the value chosen for each clash.
        """
        changes = {
            key: gap(value) if key in plan.gaps else value
            for key, value in merge.resolve(plan, resolved).items()
            if str(value) != str(doc.get(key))
        }
        if changes:
            _fields_apply(app, doc, changes)
        else:
            app.log_line(f"{source}: kept {doc.get('ref', '')} as it was")

    _ask_clashes(app, plan, ["this document", source], apply)


def _merge_apply(app: Any, plan: Any, resolved: dict[str, Any]) -> None:
    """Merge, then push one undo step for all of it.

    The step covers the folded-in folders, the survivor's fields and every file that moved. Redo repeats
    this merge, with the same answers, rather than asking again; one that meets the survivor's
    `info.yaml` changed on disk keeps the step for `ctrl+r`. `undo.strategy = "none"` records nothing
    and
    clears the stack, as a delete does.

    Args:
        app: The running `PtuiApp`.
        plan: The `merge.Plan`.
        resolved: Key to the value chosen for each clash.
    """
    back = _merge_run(app, plan, resolved)
    if back is None:
        return
    if app.config.get("undo.strategy", "trash") == "none":
        app.history.clear()
        return
    undos = [back]

    def redo() -> None:
        """Repeat the merge with the same answers.

        Raises:
            RuntimeError: When the merge could not be repeated.
            StaleError: When the survivor's `info.yaml` changed on disk.
        """
        stale: list[Any] = []
        again = _merge_run(app, plan, resolved, stale)
        _raise_stale(app, stale)  # its files went back with the refused write, so the next try starts over
        if again is None:
            raise RuntimeError("the merge could not be repeated; see the log")
        undos[0] = again  # the new run is what the next undo reverses

    label = f"merge {len(plan.others) + 1} documents into {plan.survivor.get('ref', '')}"
    app.history.push(undo.Step(label, lambda: undos[0](), redo))


def _merge_run(
    app: Any, plan: Any, resolved: dict[str, Any], stale: list[Any] | None = None
) -> Callable[[], None] | None:
    """Do the merge.

    Returns its undo, or None when nothing was changed. A survivor changed on disk goes to
    `_reread`, or into `stale` as in `_write_field`.

    Args:
        app: The running `PtuiApp`.
        plan: The `merge.Plan`.
        resolved: Key to the value chosen for each clash.
        stale: Receives the survivor when its file changed; `None` rereads it at once.

    Returns:
        A function of no arguments that undoes the merge, or `None`.
    """
    data = merge.resolve(plan, resolved)
    rules = place.Rules.from_config(app.config)
    trash_dir = app.config.as_path("undo.trash_dir") or Path.home() / ".local/share/ptui/trash"

    # Files first, and resolved against the folder they still live in: an entry
    # may be relative to a folder that is about to be trashed.
    entries = place.entries(plan.survivor)
    home = Path(plan.survivor.get_main_folder())
    placed, adopted = [], []
    for doc in plan.others:
        folder = place.normal(Path(doc.get_main_folder() or "."))
        for entry in place.entries(doc):
            source = place.resolve(doc, entry)
            if not source.exists():
                app.log_line(f"[yellow]missing file, not merged:[/] {entry}")
                continue
            result = place.place(plan.survivor, source, rules, previous=None)
            new = result.entry
            if result.status == "unmanaged":
                # In place means in the document's folder, and the loser's is
                # about to go: the survivor's folder is where the file now lives.
                # One outside it stays where it is and is merely referenced.
                if folder in place.normal(source).parents:
                    try:
                        destination = place.adopt(source, home)
                    except OSError as error:
                        app.log_line(f"[red]{source.name} not merged:[/] {error}")
                        continue
                    if not source.exists():
                        adopted.append((source, destination))
                    source = destination
                new = place._entry(source, plan.survivor, "keep", entry)
            elif result.status == "ok":
                placed.append(result)
            if new is None:
                app.log_line(f"[yellow]{result.status}[/] {source.name}: {result.message}")
            elif new not in entries:
                entries.append(new)
    if entries != place.entries(plan.survivor):
        data["files"] = entries
    before = {key: plan.survivor.get(key) for key in data}

    def files_back() -> None:
        """Take every placed and adopted file back where it came from."""
        for result in placed:
            place.rollback(result)
        for source, destination in adopted:
            place._reserve(destination, source)
            destination.unlink()

    try:
        safewrite.edit(plan.survivor, lambda info: _put_all(info, data))
    except Exception as error:
        files_back()  # file first, info.yaml second: a write that did not land takes the files back
        if not isinstance(error, safewrite.StaleError):
            app.log_line(f"[red]merge failed:[/] {error}")
        elif stale is None:
            _reread(app, plan.survivor)
        else:
            stale.append(plan.survivor)
        return None

    # Moving the folder is not enough: papis keeps its own index, and a document
    # whose folder has gone still comes back from the cache. `papis rm` pairs the
    # two, so this does too.
    import papis.database

    database = papis.database.get(app.config.get("general.library") or None)
    trashed: list[tuple[Path, Path]] = []
    for doc in plan.others:
        folder = doc.get_main_folder()
        if not folder:
            continue
        try:
            trashed.append((Path(folder), place.trash(Path(folder), trash_dir)))
        except Exception as error:
            app.log_line(f"[red]could not trash {folder}:[/] {error}")
            continue
        try:
            database.delete(doc)
        except Exception as error:
            app.log_line(f"[yellow]folder trashed but the index kept it:[/] {error}")

    app.marks.clear()
    reload(app)
    app.log_line(
        f"merged {len(plan.others) + 1} -> {plan.survivor.get('ref', '')}: "
        f"{len(data)} field(s), {len(entries)} file(s), {len(trashed)} folder(s) trashed"
    )
    for _, folder in trashed:
        app.log_line(f"[dim]trashed {folder}[/]")
    app.query_one("#log-pane").display = True

    def back() -> None:
        """Undo the merge: survivor first, then the folders, then the files."""
        # The survivor first: a write refused as stale must leave everything else alone.
        safewrite.edit(plan.survivor, lambda info: _put_all(info, before))
        restored = undo.restore(trashed)  # the folders, so the files have somewhere to go
        files_back()
        reload(app, rescan=True)  # papis forgot the folded-in documents; walk the library again
        app.log_line(f"unmerged {plan.survivor.get('ref', '')}: {len(restored)} folder(s) back from the trash")

    return back


# ── doctor ──────────────────────────────────────────────────────────────────


def _checks(app: Any, checks: str = "") -> list[str]:
    """Read the check names for a doctor command, logging the ones papis does not know.

    Args:
        app: The running `PtuiApp`.
        checks: Space-separated names; empty means `doctor.checks`.

    Returns:
        The names as configured or given.
    """
    configured = checks.split() if checks else app.config.get("doctor.checks", [])
    for name in doctor.unknown_checks(configured):
        app.log_line(f"[yellow]doctor: no such check[/] {name}")
    return configured


def _doctor_targets(app: Any, current: bool) -> list[Any]:
    """Pick the documents a doctor command looks at.

    `current = true` means this document only, ignoring marks: the difference between `! d` and `! !`.
    Without it the whole narrowed set is the target, marks first: doctoring only the document under the
    cursor is what `! d` is for, and a library-wide check needs a library-sized set to say anything.

    Args:
        app: The running `PtuiApp`.
        current: Look at the document under the cursor alone.

    Returns:
        The documents, empty when there is no target.
    """
    if not current:
        return app.targets if app.marks else app.rows
    doc = app.current
    return [doc] if doc is not None else []


def _findings(app: Any, current: bool = False, checks: str = "") -> list[tuple[Any, doctor.Finding]]:
    """Every finding over the target set, freshly scanned. Read-only.

    Args:
        app: The running `PtuiApp`.
        current: Look at the document under the cursor alone.
        checks: Space-separated names; empty means `doctor.checks`.

    Returns:
        `(doc, finding)` pairs, in document order.
    """
    names = _checks(app, checks)
    return [(doc, finding) for doc in _doctor_targets(app, current) for finding in doctor.scan(doc, names)]


def _label(doc: Any, finding: doctor.Finding) -> str:
    """Describe a finding in one line.

    Args:
        doc: The document it is about.
        finding: The finding.

    Returns:
        The ref, the message, and a note when there is no automatic fix.
    """
    ref = doc.get("ref") or library.doc_id(doc)
    fixable = "" if finding.fix_action else "  (no automatic fix)"
    return f"{ref}  {finding.msg}{fixable}"


@command("doctor.run", "run doctor")
def doctor_run(app: Any, checks: str = "", current: bool = False) -> None:
    """Scan, report into the log, and narrow the list to what has findings.

    **Never fixes** — `doctor.fix` does that, on request. The narrowed list *is*
    the findings view: the cursor moves through the affected documents and the
    info pane shows each one's findings, so there is no modal to dismiss and
    every other verb still works on the row. `escape` drops the narrow.

    `current` scans this document only and does not narrow — it is the manual
    re-check after an edit, which is what refreshes the info pane.

    Args:
        app: The running `PtuiApp`.
        checks: Space-separated names; empty means `doctor.checks`.
        current: Scan this document only, without narrowing.
    """
    names = _checks(app, checks)
    targets = _doctor_targets(app, current)
    found = [(doc, check_result) for doc in targets for check_result in doctor.scan(doc, names)]
    if not current:
        # Once, over the whole set: these checks compare documents against each
        # other, so they are meaningless per document and stateful across runs.
        found += doctor.scan_library(targets, names)
    for doc, finding in found:
        app.log_line(f"[yellow]{finding.name}[/] {_label(doc, finding)}")
    app.log_line(f"doctor: {len(found)} finding(s) over {len(targets)} document(s)")
    app.query_one("#log-pane").display = True
    if current:
        app.refresh_info()
        return
    # By the checks that ran: the cache keeps every other check's findings too
    app.doctor_only = tuple(doctor.check_names(names))
    app.refilter()


@command("doctor.fix_pick", "fix one finding…")
def doctor_fix_pick(app: Any) -> None:
    """Choose one of this document's findings to fix.

    A picker used as a picker: reporting is the info pane's job, and this asks
    *which one*. Nothing is written by opening it.

    Args:
        app: The running `PtuiApp`.
    """
    found = _findings(app, current=True)
    if not found:
        app.log_line("doctor: nothing to fix here")
        return
    items = [
        ui.Item(label=_label(doc, finding), value=index, hint=finding.name)
        for index, (doc, finding) in enumerate(found)
    ]

    def apply(index: int, _invert: bool) -> None:
        """Fix the picked finding.

        Args:
            index: Its position in the list.
            _invert: The picker's `shift+enter` flag; unused.
        """
        _fix(app, [found[index]])

    ui.pick(app, items, title="Fix which finding?")(apply)


def _apply_one(app: Any, doc: Any, finding: doctor.Finding) -> dict[str, tuple[Any, Any]]:
    """One fix, written. Returns what `doctor.fix` does — empty when nothing landed.

    Args:
        app: The running `PtuiApp`.
        doc: The document the finding is about.
        finding: The finding to fix.

    Returns:
        Changed key to `(old value, new value)`, empty when nothing landed.
    """
    if finding.fix_action is None:
        app.log_line(f"[yellow]no automatic fix for[/] {finding.name}: {finding.msg}")
        return {}
    try:
        changed = doctor.fix(doc, finding)
    except safewrite.StaleError:
        _reread(app, doc)
        return {}
    except Exception as error:
        app.log_line(f"[red]fix failed:[/] {error}")
        return {}
    if not changed:  # papis's fix had nothing to go on — `keys-missing` cannot invent an author
        app.log_line(f"[yellow]{finding.name} on {doc.get('ref', '')}: the fix changed nothing[/]")
        return {}
    app.log_line(f"fixed {finding.name} on {doc.get('ref', '')}: {', '.join(changed)}")
    doctor.scan(doc, _checks(app))  # the write invalidated the cache; the pane reads it
    app.refresh_rows()
    return changed


def _fix(app: Any, found: list[tuple[Any, doctor.Finding]]) -> int:
    """Apply each finding, then push one undo step for every fix that landed.

    Returns how many did.

    Undo writes back what each write replaced, newest first — two fixes may
    touch one key — and redo writes what they wrote, as `_fields_apply` does
    for one document.

    Args:
        app: The running `PtuiApp`.
        found: `(doc, finding)` pairs to fix, in order.

    Returns:
        How many fixes landed.
    """
    fixed = [(doc, changed) for doc, finding in found if (changed := _apply_one(app, doc, finding))]
    if fixed:
        docs = len({library.doc_id(doc) for doc, _ in fixed})
        app.history.push(
            undo.Step(
                f"fix {len(fixed)} finding(s) on {docs} document(s)",
                lambda: _rewrite(app, fixed[::-1], 0),
                lambda: _rewrite(app, fixed, 1),
            )
        )
    return len(fixed)


@command("doctor.fix", "fix doctor findings")
def doctor_fix(app: Any, checks: str = "", current: bool = False) -> None:
    """Apply every fixable finding over the target set.

    A finding cannot travel through a `keys.toml` argument, so the command
    reachable by name is the batch one — the same shape as every other verb
    here. `doctor.fix_pick` is the one-finding path. `checks` narrows it to
    named checks — `:doctor.fix field-type` is one check across the library,
    where `! a` is everything at once.

    Fixes on more than one document confirm first, and the run is one undo step.

    Args:
        app: The running `PtuiApp`.
        checks: Space-separated names; empty means `doctor.checks`.
        current: Fix the document under the cursor alone.
    """
    found = _findings(app, current, checks)
    if not found:
        app.log_line("doctor: nothing to fix")
        return
    fixable = [(doc, finding) for doc, finding in found if finding.fix_action]
    skipped = len(found) - len(fixable)

    def run() -> None:
        """Apply the fixable findings and report how many landed."""
        fixed = _fix(app, fixable)
        app.log_line(f"doctor: fixed {fixed} of {len(fixable)}, {skipped} with no automatic fix")
        if skipped:
            app.query_one("#log-pane").display = True

    docs = len({library.doc_id(doc) for doc, _ in fixable})
    _confirm_batch(app, docs, f"fix {len(fixable)} finding(s)", run)


# ── delete and undo ─────────────────────────────────────────────────────────


def trash_dir(app: Any) -> Path:
    """Find the trash folder.

    Args:
        app: The running `PtuiApp`.

    Returns:
        `undo.trash_dir`, or the default under `~/.local/share/ptui`.
    """
    return app.config.as_path("undo.trash_dir") or Path.home() / ".local/share/ptui/trash"


def _old_trash(app: Any, days: int) -> tuple[Path, list[Path], int]:
    """Find what has been in the trash longer than `days`.

    Judged by the trash's own index, not the mtime a moved folder keeps.

    Args:
        app: The running `PtuiApp`.
        days: The age limit.

    Returns:
        `(trash folder, paths past the limit, their total size in bytes)`.
    """
    import time

    where = trash_dir(app)
    if not where.is_dir():
        return where, [], 0
    cutoff = time.time() - days * 86400
    old = [where / name for name, at in place.trashed_at(where).items() if at < cutoff]
    return (
        where,
        old,
        sum(file_path.stat().st_size for path in old for file_path in [path, *path.rglob("*")] if file_path.is_file()),
    )


def _purge(where: Path, old: list[Path]) -> None:
    """Delete paths from the trash for good, and drop them from its index.

    Args:
        where: The trash folder.
        old: The paths to delete.
    """
    import shutil

    for path in old:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    place.trashed_at(where)  # drop them from the index


def auto_empty_trash(app: Any) -> None:
    """Delete what has outlived `undo.trash_retention_days`, at startup.

    Runs with `undo.auto_empty_trash`, with one line saying how much went. The trash outlives the
    session that filled it, but `u` does not, so past the retention it is only a safety net.

    Args:
        app: The running `PtuiApp`.
    """
    if not app.config.get("undo.auto_empty_trash", True):
        return
    days = app.config.get("undo.trash_retention_days", 30)
    try:
        where, old, size = _old_trash(app, days)
        if old:
            _purge(where, old)
    except OSError as error:  # a trash that cannot be read must not stop the start
        app.log_line(f"[yellow]trash not emptied: {error}[/]")
        return
    if old:
        app.log_line(f"trash: deleted {len(old)} item(s) older than {days} day(s) ({size / 1e6:.1f} MB)")


@command("lib.empty_trash", "empty the trash")
def lib_empty_trash(app: Any, days: int | None = None) -> None:
    """Delete what has been in the trash longer than `days`, after saying how much that is.

    For good, and an undo step that pointed into it has nothing left to restore. Startup does the same
    unasked while `undo.auto_empty_trash` is on.

    Args:
        app: The running `PtuiApp`.
        days: The age limit; `None` means `undo.trash_retention_days`.
    """
    days = app.config.get("undo.trash_retention_days", 30) if days is None else days
    where, old, size = _old_trash(app, days)
    if not where.is_dir():
        app.log_line("the trash is empty")
        return
    if not old:
        app.log_line(f"nothing in {where} is older than {days} day(s)")
        return

    def purge(ok: bool, _invert: bool) -> None:
        """Delete the old entries when confirmed.

        Args:
            ok: The answer to the question.
            _invert: The picker's `shift+enter` flag; unused.
        """
        if not ok:
            return
        _purge(where, old)
        app.log_line(f"deleted {len(old)} trashed item(s) for good ({size / 1e6:.1f} MB)")

    items = [
        ui.Item(label=f"delete {len(old)} trashed item(s) for good ({size / 1e6:.1f} MB)", value=True),
        ui.Item(label="cancel", value=False),
    ]
    ui.pick(app, items, title=f"Empty the trash of what is older than {days} day(s)?")(purge)


def library_dir(app: Any) -> Path:
    """Where the current library lives on disk, straight from papis.

    Args:
        app: The running `PtuiApp`.

    Returns:
        The library's folder.
    """
    import papis.config

    name = app.config.get("general.library") or papis.config.get_lib_name()
    return Path(papis.config.get_lib_from_name(name).path)


@dataclass(frozen=True, slots=True)
class Doomed:
    """One file a delete would take with it, and why it is offered or not.

    Attributes:
        doc: The document the file belongs to.
        entry: The `files` entry as stored.
        path: The resolved file.
        choice: How the dialog shows it.
    """

    doc: Any
    entry: str
    path: Path
    choice: ui.FileChoice


def _doomed_files(app: Any, targets: list[Any]) -> list[Doomed]:
    """Every file of the targets that lives *outside* its document folder.

    Deviation from SPEC, deliberate and specced: the files inside the folder
    are not offered, because there is no decision to make — the folder is what
    is being removed and they travel with it. What needs a checkbox is a file
    under `pdf_root` or somewhere else entirely.

    Checked by default only under a managed root, and never when another
    document points at the same file: with a shared `pdf_root` and a script
    appending entries, two documents on one file is a realistic accident.

    Inside and outside are decided on normalised paths: `../../pdfs/x.pdf` is
    written under the folder but lives outside it, and does not go with it.
    The other users are looked for in the whole library, not the scope: a
    document the scope hides still loses the file if it is trashed.

    Args:
        app: The running `PtuiApp`.
        targets: The documents being deleted.

    Returns:
        One `Doomed` per file outside its document's folder.
    """
    import papis.database

    everyone = papis.database.get(app.config.get("general.library") or None).get_all_documents()
    pdf_root = app.config.as_path("files.pdf_root")
    root = place.normal(pdf_root) if pdf_root else None
    doomed = []
    for doc in targets:
        folder = place.normal(Path(doc.get_main_folder() or "."))
        for entry in place.entries(doc):
            path = place.resolve(doc, entry)
            where = place.normal(path)
            if folder in where.parents:
                continue
            managed = root is not None and root in where.parents
            others = [
                other.get("ref") or library.doc_id(other)
                for other in everyone
                if library.doc_id(other) != library.doc_id(doc)
                and any(place.same_file(place.resolve(other, stored), path) for stored in place.entries(other))
            ]
            note = "" if managed else "outside the managed roots"
            if others:
                note = f"also in {', '.join(others[:3])}"
            doomed.append(Doomed(doc, entry, path, ui.FileChoice(str(where), managed and not others, note)))
    return doomed


@command("doc.delete", "delete document(s)")
def doc_delete(app: Any, force: bool = False) -> None:
    """Remove documents from the library, recoverably.

    Confirms against the **total** number of targets with a preview of what
    they are — marking 200, narrowing to 3 and pressing `doomed_file doomed_file` must not delete
    200 on the strength of what is on screen. `force` skips the dialog and
    keeps no files, which is the batch-script shape.

    Args:
        app: The running `PtuiApp`.
        force: Skip the dialog, taking only the files that are ticked by default.
    """
    targets = app.targets
    if not targets:
        return
    doomed = _doomed_files(app, targets)
    if force:
        _delete_apply(app, targets, [doomed_file for doomed_file in doomed if doomed_file.choice.checked])
        return

    shown = [f"  {ui.literal(str(doc.get('ref') or library.doc_id(doc)))}" for doc in targets[:10]]
    if len(targets) > 10:
        shown.append(f"  [dim]… and {len(targets) - 10} more[/]")
    inside = sum(len(place.entries(doc)) for doc in targets) - len(doomed)
    if inside:
        shown.append(f"[dim]{inside} file(s) inside the folders go with them[/]")

    def confirm(chosen: list[int] | None) -> None:
        """Delete with the ticked files, unless the dialog was cancelled.

        Args:
            chosen: Indices into the doomed files, or `None` when cancelled.
        """
        if chosen is not None:
            _delete_apply(app, targets, [doomed[index] for index in chosen])

    app.push_screen(
        ui.ConfirmDelete(
            shown,
            [doomed_file.choice for doomed_file in doomed],
            title=f"Delete {len(targets)} document(s)",
        ),
        ui.guarded("delete", confirm),
    )


def _delete_apply(app: Any, targets: list[Any], files: list[Doomed]) -> None:
    """Delete, then push one undo step for the whole operation.

    Redo repeats *this* delete — the same documents by `papis_id`, the same
    files — rather than calling `doc.delete` again, which would act on whatever
    the cursor or the marks hold by then.

    Args:
        app: The running `PtuiApp`.
        targets: The documents to delete.
        files: The files outside their folders that go too.
    """
    strategy = app.config.get("undo.strategy", "trash")
    label = f"delete {len(targets)} document(s)"
    paths = [doomed.path for doomed in files]
    try:
        back = [_delete_run(app, targets, paths, label)]
    except Exception as error:
        app.log_line(f"[red]delete failed:[/] {error}")
        return

    if strategy == "none":
        app.history.clear()  # nothing here is undoable; do not offer the last one
    else:
        ids = {library.doc_id(doc) for doc in targets}

        def redo() -> None:
            """Repeat this delete on the same documents, found by `papis_id`.

            Raises:
                RuntimeError: When a deleted document is no longer in the library.
            """
            import papis.database

            # The whole library, not `app.docs`: the scope may have changed since the undo.
            database = papis.database.get(app.config.get("general.library") or None)
            again = [database.find_by_id(document_id) for document_id in ids]
            if None in again:  # without the document, its files would be trashed alone
                raise RuntimeError("cannot redo: a deleted document is no longer in the library")
            back[0] = _delete_run(app, again, paths, label)  # the new run is what undo reverses
            reload(app)

        app.history.push(undo.Step(label, lambda: back[0](), redo))

    app.marks.clear()
    reload(app)
    app.log_line(
        f"deleted {len(targets)} document(s), {len(files)} file(s)" + ("" if strategy == "none" else " — u to undo")
    )
    app.query_one("#log-pane").display = True


def _delete_run(app: Any, targets: list[Any], paths: list[Path], label: str) -> Callable[[], None]:
    """Trash `paths`, remove the folders per `undo.strategy`, and drop the documents from papis's index.

    Args:
        app: The running `PtuiApp`.
        targets: The documents to delete.
        paths: The files outside their folders that go too.
        label: What the step is called; the commit subject under git.

    Returns:
        A function of no arguments that undoes exactly this run.

    Raises:
        RuntimeError: When the strategy is `git` and the library is not a repository.
    """
    import papis.database

    folders = [Path(doc.get_main_folder()) for doc in targets if doc.get_main_folder()]
    root = None
    if app.config.get("undo.strategy", "trash") == "git":
        # Before anything moves: finding out after the files went to the trash
        # left them there, with the delete failed and no undo step for it.
        root = undo.git_root(folders[0]) if folders else None
        if root is None:
            raise RuntimeError("undo.strategy = git, but the library is not a git repository")

    where = trash_dir(app)
    moved: list[tuple[Path, Path]] = []
    for path in paths:
        try:
            moved.append((path, place.trash(path, where)))
        except Exception as error:
            app.log_line(f"[red]could not trash {path.name}:[/] {error}")

    if root is not None:
        back = _delete_with_git(app, root, folders, label, moved)
    else:
        for folder in folders:
            moved.append((folder, place.trash(folder, where)))
        back = partial(_undo_trash, app, moved)

    # Papis keeps its own index, and a document whose folder has gone still
    # comes back from the cache — the same pairing `papis rm` does.
    database = papis.database.get(app.config.get("general.library") or None)
    for doc in targets:
        try:
            database.delete(doc)
        except Exception as error:
            app.log_line(f"[yellow]folder gone but the index kept it:[/] {error}")
    return back


def _delete_with_git(
    app: Any, root: Path, folders: list[Path], label: str, moved: list[tuple[Path, Path]]
) -> Callable[[], None]:
    """`git rm` the folders in one commit; the returned undo reverts it.

    One ptui operation is one commit, so the log reads as a history of what the
    user did. Files outside the repo were already trashed by the caller, and
    their undo rides along with the revert.

    Args:
        app: The running `PtuiApp`.
        root: The work tree.
        folders: The document folders to remove.
        label: The commit subject.
        moved: `(original, path in the trash)` pairs the caller already trashed.

    Returns:
        A function of no arguments that reverts the commit and restores `moved`.
    """
    commit = undo.git_delete(root, folders, label)

    def back() -> None:
        """Revert the commit and bring the trashed files back."""
        undo.git_revert(root, commit)
        undo.restore(moved)
        reload(app, rescan=True)
        app.log_line(f"reverted {commit[:8]}")

    return back


def _undo_trash(app: Any, moved: list[tuple[Path, Path]]) -> None:
    """Bring trashed paths back and reload.

    Args:
        app: The running `PtuiApp`.
        moved: `(original, path in the trash)` pairs.
    """
    restored = undo.restore(moved)
    reload(app, rescan=True)
    app.log_line(f"restored {len(restored)} path(s) from the trash")


def _press(app: Any, command_name: str) -> str:
    """How to run `command_name` from here: press its keys, or, with none bound, run it by name.

    Args:
        app: The running `PtuiApp`.
        command_name: Dotted command name.

    Returns:
        `press <keys>`, or `run <name>`.
    """
    keys = app.keymap.for_command(app.mode, command_name) or app.keymap.for_command("list", command_name)
    return f"press {keys}" if keys else f"run {command_name}"


def _not_done(app: Any, verb: str, error: safewrite.StaleError) -> str:
    """Say what `u` or `ctrl+r` says when a file changed on disk stopped it.

    That is that it did not happen, or on how many documents it did and which are left.

    Args:
        app: The running `PtuiApp`.
        verb: `undo` or `redo`.
        error: The `StaleError` raised.

    Returns:
        The log line, as markup.
    """
    again = _press(app, f"app.{verb}")
    if error.done:
        return f"[yellow]{verb} done on {error.done} document(s), but {error}; {again} to {verb} the rest[/]"
    return f"[yellow]{verb} did not happen: {error}; {again} to try again[/]"


@command("app.undo", "undo")
def app_undo(app: Any) -> None:
    """Undo the newest step.

    Args:
        app: The running `PtuiApp`.
    """
    try:
        step = app.history.undo()
    except safewrite.StaleError as error:  # the step is still there (`undo.History`)
        app.log_line(_not_done(app, "undo", error))
        return
    if step is None:
        app.log_line("[yellow]nothing to undo[/]")
        return
    app.refresh_rows()
    app.log_line(f"undo: {step.label}")


@command("app.redo", "redo")
def app_redo(app: Any) -> None:
    """Redo the newest undone step.

    Args:
        app: The running `PtuiApp`.
    """
    try:
        step = app.history.redo()
    except safewrite.StaleError as error:
        app.log_line(_not_done(app, "redo", error))
        return
    if step is None:
        app.log_line("[yellow]nothing to redo[/]")
        return
    app.refresh_rows()
    app.log_line(f"redo: {step.label}")


def warn_untracked(app: Any) -> None:
    """`strategy = "git"`: say so, once, if the files are not actually in git.

    SPEC: do not advertise an undo that isn't one. A gitignored `pdf_root` is
    the normal case, and it is covered by the trash — what would be a lie is
    letting the user believe the commit holds their PDFs.

    Args:
        app: The running `PtuiApp`.
    """
    if app.config.get("undo.strategy") != "git" or not app.config.get("undo.git_warn_untracked", True):
        return
    root = undo.git_root(library_dir(app))
    if root is None:
        app.log_line("[yellow]undo.strategy = git, but the library is not a git repository[/]")
        return
    pdf_root = app.config.as_path("files.pdf_root")
    if pdf_root and not undo.git_tracks(root, pdf_root):
        app.log_line(f"[yellow]git does not track {pdf_root}; those files undo via the trash[/]")


@command("app.quit", "quit")
def app_quit(app: Any) -> None:
    """Quit.

    Args:
        app: The running `PtuiApp`.
    """
    app.exit()


# Modal-only: `SelectList` reads these straight out of `[modes.picker]`. They are
# registered so the keymap check does not flag them as unknown.
@command("picker.confirm", "confirm")
def picker_confirm(app: Any, invert: bool = False) -> None:
    """Say that this only applies inside a picker.

    Args:
        app: The running `PtuiApp`.
        invert: `[modes.picker]`'s flag; unused outside a picker.
    """
    app.log_line("[dim]picker.confirm only applies inside a picker[/]")


@command("picker.cancel", "cancel")
def picker_cancel(app: Any) -> None:
    """Say that this only applies inside a picker.

    Args:
        app: The running `PtuiApp`.
    """
    app.log_line("[dim]picker.cancel only applies inside a picker[/]")
