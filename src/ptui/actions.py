"""Command implementations. Every one is registered in `commands.REGISTRY`.

Handlers take the app first and are plain functions — nothing here imports the
app module, so `app.py` can import this at load time to register everything.
"""

from __future__ import annotations

from typing import Any

from textual.widgets import DataTable

from ptui import library
from ptui.commands import command

# ── navigation ──────────────────────────────────────────────────────────────


def _table(app: Any) -> DataTable:
    return app.query_one(DataTable)


def _move(app: Any, delta: int) -> None:
    table = _table(app)
    row = max(0, min(table.row_count - 1, table.cursor_row + delta))
    table.move_cursor(row=row)
    app.refresh_info()
    app.refresh_status()


@command("nav.down", "move down")
def nav_down(app: Any, count: int = 1) -> None:
    _move(app, count)


@command("nav.up", "move up")
def nav_up(app: Any, count: int = 1) -> None:
    _move(app, -count)


@command("nav.top", "first document")
def nav_top(app: Any) -> None:
    _move(app, -_table(app).row_count)


@command("nav.bottom", "last document")
def nav_bottom(app: Any) -> None:
    _move(app, _table(app).row_count)


@command("nav.page_down", "page down")
def nav_page_down(app: Any) -> None:
    _move(app, max(1, _table(app).size.height - 2))


@command("nav.page_up", "page up")
def nav_page_up(app: Any) -> None:
    _move(app, -max(1, _table(app).size.height - 2))


# ── panes ───────────────────────────────────────────────────────────────────


@command("pane.focus", "focus a pane")
def pane_focus(app: Any, pane: str) -> None:
    app.focus_pane(pane)


@command("pane.cycle", "next pane")
def pane_cycle(app: Any, back: bool = False) -> None:
    from ptui.app import PANES

    index = PANES.index(app.mode) if app.mode in PANES else 0
    app.focus_pane(PANES[(index - 1 if back else index + 1) % len(PANES)])


@command("pane.toggle", "show/hide a pane")
def pane_toggle(app: Any, pane: str) -> None:
    try:
        widget = app.query_one(f"#{pane}-pane")
    except Exception:
        app.log_line(f"[yellow]no {pane} pane in v0[/]")
        return
    widget.display = not widget.display


@command("pane.toggle_layout", "horizontal/vertical split")
def pane_toggle_layout(app: Any) -> None:
    panes = app.query_one("#panes")
    horizontal = panes.styles.layout is None or str(panes.styles.layout) == "horizontal"
    panes.styles.layout = "vertical" if horizontal else "horizontal"


@command("pane.resize", "adjust the split")
def pane_resize(app: Any, delta: float) -> None:
    table = _table(app)
    ratio = min(0.9, max(0.1, (table.size.width / max(1, app.size.width)) + delta))
    table.styles.width = f"{int(ratio * 100)}%"


@command("app.log", "operation log")
def app_log(app: Any) -> None:
    pane = app.query_one("#log-pane")
    pane.display = not pane.display
    if pane.display:
        app.focus_pane("log")


# ── queries ─────────────────────────────────────────────────────────────────


def scope(app: Any, query: str) -> None:
    """Run a papis query and rebuild the scoped set. Marks survive by design."""
    aliases = app.cfg.get("query.aliases", {})
    expanded = library.expand_aliases(query, aliases)
    app.scope_query = query
    try:
        app.docs = library.scope(expanded, app.cfg.get("general.library"))
    except Exception as exc:
        app.log_line(f"[red]query failed:[/] {exc}")
        return
    if expanded != query:
        app.log_line(f"[dim]scope -> {expanded}[/]")
    app.apply_sort()
    app.refilter()


def prompt_result(app: Any, kind: str, value: str) -> None:
    """Dispatch a prompt whose result is not a query (set by later commands)."""
    app.log_line(f"[yellow]unhandled prompt {kind!r}: {value}[/]")


@command("query.scope", "search (papis query)")
def query_scope(app: Any, q: str | None = None) -> None:
    if q is None:
        app.open_prompt("scope", "papis query", app.scope_query)
    else:
        scope(app, q)


@command("query.narrow", "narrow (instant)")
def query_narrow(app: Any, q: str | None = None) -> None:
    if q is None:
        app.open_prompt("narrow", "narrow", app.narrow_query)
    else:
        app.apply_narrow(q)


@command("query.clear", "clear the narrow filter")
def query_clear(app: Any) -> None:
    app.refilter("")


@command("app.reload", "reload library")
def reload(app: Any) -> None:
    import papis.database

    papis.database.clear_cached()
    scope(app, app.scope_query)


# ── sorting ─────────────────────────────────────────────────────────────────


@command("sort.by", "sort by a key")
def sort_by(app: Any, key: str, reverse: bool | None = None) -> None:
    if reverse is None:
        presets = {p["key"]: p for p in app.cfg.get("list.sort_presets", [])}
        reverse = presets.get(key, {}).get("dir", "asc") == "desc"
    app.sort_key, app.sort_reverse = key, reverse
    app.apply_sort()
    app.refilter()


@command("sort.reverse", "reverse sort")
def sort_reverse(app: Any) -> None:
    sort_by(app, app.sort_key, not app.sort_reverse)


# ── marks ───────────────────────────────────────────────────────────────────


@command("mark.toggle", "mark/unmark")
def mark_toggle(app: Any) -> None:
    doc = app.current
    if doc is None:
        return
    key = library.doc_id(doc)
    app.marks.symmetric_difference_update({key})
    app.refresh_rows()
    if app.cfg.get("marks.advance", True):
        _move(app, 1)
    app.refresh_hints()


@command("mark.all_filtered", "mark all filtered")
def mark_all_filtered(app: Any) -> None:
    app.marks |= {library.doc_id(d) for d in app.rows}
    app.refresh_rows()


@command("mark.invert", "invert marks")
def mark_invert(app: Any) -> None:
    app.marks ^= {library.doc_id(d) for d in app.rows}
    app.refresh_rows()


@command("mark.clear", "clear marks")
def mark_clear(app: Any) -> None:
    app.marks.clear()
    app.refresh_rows()


@command("mark.show_only", "toggle marked-only")
def mark_show_only(app: Any) -> None:
    app.marked_only = not app.marked_only
    app.refilter()


# ── app ─────────────────────────────────────────────────────────────────────


@command("app.escape", "cancel")
def app_escape(app: Any) -> None:
    """Resolve per `escape_chain`. Never clears marks — only `m c` does."""
    for step in app.km.option("escape_chain", ["modal", "narrow"]):
        if step == "modal" and app.prompt_kind:
            app.close_prompt()
            return
        if step == "narrow" and app.narrow_query:
            app.refilter("")
            return
    app.pending = ()


@command("keymap.check", "check keymap conflicts")
def keymap_check(app: Any) -> None:
    problems = [*app.km.conflicts(), *app.km.unknown_commands]
    for problem in problems:
        app.log_line(f"[yellow]{problem}[/]")
    app.log_line(f"keymap: {len(problems)} problem(s)")
    app.query_one("#log-pane").display = True


@command("app.config_check", "check config")
def config_check(app: Any) -> None:
    for key in app.cfg.unknown:
        app.log_line(f"[yellow]unknown config key:[/] {key}")
    app.log_line(f"config: {len(app.cfg.unknown)} unknown key(s) in {app.cfg.path or 'defaults'}")
    app.query_one("#log-pane").display = True


@command("app.quit", "quit")
def app_quit(app: Any) -> None:
    app.exit()
