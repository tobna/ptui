"""Command implementations. Every one is registered in `commands.REGISTRY`.

Handlers take the app first and are plain functions — nothing here imports the
app module, so `app.py` can import this at load time to register everything.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from textual.widgets import DataTable

from ptui import clip, commands, doctor, fetch, library, merge, place, safewrite, ui, undo
from ptui.commands import REGISTRY, command

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
    if pane == "list":
        app.log_line("[yellow]the list pane cannot be hidden[/]")
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
    app.layout_auto = False  # an explicit choice wins over `ui.layout = "auto"`
    app.side_by_side = not app.side_by_side
    app.apply_split()


@command("pane.resize", "adjust the split")
def pane_resize(app: Any, delta: float) -> None:
    app.split = min(0.9, max(0.1, app.split + delta))
    app.apply_split()


@command("app.log", "operation log")
def app_log(app: Any) -> None:
    app.focus_pane("list" if app.query_one("#log-pane").display else "log")


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
    """Dispatch a prompt whose result is not a query."""
    if kind == "add":
        add_form(app, Path(value).expanduser())
    elif kind == "set":
        # `key value with spaces` — the value is the rest of the line, not a
        # shell word: this is a prompt, and quoting a title would be absurd.
        field, _, text = value.strip().partition(" ")
        if field:
            doc_set(app, field, text.strip())
    elif kind in ("tag", "untag"):
        (doc_tag if kind == "tag" else doc_untag)(app, value)
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
    """
    if not uri.strip():
        return
    if source == "bib":
        _bib_pick(app, Path(uri).expanduser())
        return
    app.log_line(f"[dim]fetching from {source}…[/]")

    def job() -> None:
        try:
            data, files = fetch.from_url(uri) if source == "url" else fetch.fetch(source, uri)
        except Exception as exc:
            app.call_from_thread(app.log_line, f"[red]{source} failed:[/] {exc}")
            return
        app.call_from_thread(_fetched, app, source, data, files)

    app.run_worker(job, thread=True, group="import")


def _fetched(app: Any, source: str, data: dict[str, Any], files: list[Path]) -> None:
    got = ", ".join(k for k in ("title", "author", "year", "doi") if data.get(k))
    app.log_line(f"{source}: {got or 'metadata'}" + (f" + {len(files)} file(s)" if files else ""))
    # papis downloads to a temp file; `place()` moves it where the rules say.
    add_form(app, files[0] if files else None, data)


def _bib_pick(app: Any, path: Path) -> None:
    """A `.bib` usually holds many entries, so choose one rather than importing
    the file. Bulk import is a different feature — see TODO E."""
    try:
        entries = fetch.bib_entries(path)
    except Exception as exc:
        app.log_line(f"[red].bib failed:[/] {exc}")
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
        lambda index, _i: add_form(app, None, entries[index])
    )


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
def reload(app: Any, rescan: bool = False) -> None:
    """Re-run the scope query against a fresh database.

    `rescan` throws papis's *on-disk* cache away as well, which is what an undo
    needs: `clear_cached` only drops the in-process handle, so a folder that has
    just come back from the trash stays invisible until papis walks the library
    again. Deleting is the cheap direction — papis wrote the document out of the
    cache when it went — and restoring is the one that has to pay for the walk.
    """
    import papis.database

    if rescan:
        papis.database.get(app.cfg.get("general.library") or None).clear()
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


@command("sort.picker", "sort by…")
def sort_picker(app: Any) -> None:
    """Presets first, then keys discovered in the library. `enter` applies the
    key's own default direction, `shift+enter` inverts it — a single global
    reverse flag is wrong half the time (author ascending, date descending)."""
    presets = app.cfg.get("list.sort_presets", [])
    items = [
        ui.Item(
            label=f"{p.get('label', p['key'])} {ui.glyph('sort_desc' if p.get('dir') == 'desc' else 'sort_asc')}",
            value=p["key"],
            hint=p["key"],
        )
        for p in presets
    ]
    if app.cfg.get("list.sort_discover_keys", True):
        known = {p["key"] for p in presets}
        # ponytail: discovered from the documents already in memory. Add the
        # worker + cache generation from SPEC if a big library makes this drag.
        items += [
            ui.Item(label=key, value=key, hint="discovered")
            for key in library.discover_keys(app.docs)
            if key not in known
        ]

    def apply(key: str, inverted: bool) -> None:
        directions = {p["key"]: p.get("dir", "asc") for p in presets}
        reverse = directions.get(key, "asc") == "desc"
        sort_by(app, key, reverse != inverted)

    ui.pick(app, items, title="Sort by", current=app.sort_key)(apply)


@command("lib.switch", "switch library")
def lib_switch(app: Any, name: str | None = None) -> None:
    import papis.config

    def apply(library_name: str, _inverted: bool = False) -> None:
        app.cfg.data["general"]["library"] = library_name
        app.marks.clear()
        reload(app)
        app.log_line(f"library: {library_name}")

    if name:
        apply(name)
        return
    current = app.cfg.get("general.library") or papis.config.get_lib_name()
    items = [ui.Item(label=lib, value=lib) for lib in papis.config.get_libs()]
    ui.pick(app, items, title="Library", current=current)(apply)


@command("lib.backfill_dates", "stamp missing time-added")
def lib_backfill_dates(app: Any) -> None:
    """Give every document in scope that lacks `time-added` the mtime of its
    `info.yaml`.

    papis 0.15 no longer writes the key, so a library carries a handful of
    documents from older papis and hundreds without — and "recently added"
    sorts the whole silent majority into one undifferentiated tail. The mtime
    is approximate (a later edit moves it) but it is the only ordering on disk,
    and it is monotone enough for a library nobody has bulk-rewritten.
    """
    planned = [
        (doc, library.stamp(Path(doc.get_info_file()).stat().st_mtime))
        for doc in app.docs
        if not doc.get(library.TIME_ADDED)
    ]
    if not planned:
        app.log_line("every document in scope already has time-added")
        return
    items = [
        ui.Item(label=f"stamp {len(planned)} documents from their file dates", value=True),
        ui.Item(label="cancel", value=False),
    ]
    ui.pick(app, items, title=f"backfill time-added — {len(planned)} documents?")(
        lambda ok, _invert: _set_apply(app, planned, library.TIME_ADDED) if ok else None
    )


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


# ── document verbs ──────────────────────────────────────────────────────────


def files_of(app: Any, doc: Any) -> list[Path]:
    """Existing files of a document, main first.

    Kind is *inferred* from `files.kind_patterns` and never written to disk — a
    parallel `kind` key would desync with a script that appends to `files`.
    """
    patterns = [p for group in app.cfg.get("files.kind_patterns", {}).values() for p in group]
    paths = [place.resolve(doc, entry) for entry in doc.get("files", [])]
    main = [p for p in paths if not any(fnmatch(p.name, pat) for pat in patterns)]
    return main + [p for p in paths if p not in main]


@command("doc.open", "open file")
def doc_open(app: Any, which: int | None = None) -> None:
    import papis.utils

    doc = app.current
    if doc is None:
        return
    paths = files_of(app, doc)
    if not paths:
        app.log_line("[yellow]no files attached[/]")
        return
    path = paths[which] if which is not None and which < len(paths) else paths[0]
    if not path.exists():
        app.log_line(f"[red]missing file:[/] {path}")
        return
    papis.utils.open_file(str(path), wait=False)
    app.log_line(f"opened {path.name}")
    if app.cfg.get("general.track_opens", False):
        _touch_opened_at(app, doc)


def _touch_opened_at(app: Any, doc: Any) -> None:
    from datetime import datetime

    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        safewrite.edit(doc, lambda data: data.__setitem__("opened_at", stamp))
    except safewrite.StaleError:
        app.log_line("[yellow]info.yaml changed on disk; press r to reload[/]")


@command("files.open_pick", "open which file…")
def files_open_pick(app: Any) -> None:
    doc = app.current
    if doc is None:
        return
    paths = files_of(app, doc)
    items = [ui.Item(label=p.name, value=i, hint="" if p.exists() else "missing") for i, p in enumerate(paths)]
    ui.pick(app, items, title="Open file")(lambda index, _invert: doc_open(app, which=index))


@command("doc.open_folder", "open folder")
def doc_open_folder(app: Any) -> None:
    import papis.api

    doc = app.current
    if doc is not None and doc.get_main_folder():
        papis.api.open_dir(doc.get_main_folder())


@command("doc.browse", "open URL/DOI")
def doc_browse(app: Any) -> None:
    import papis.commands.browse

    doc = app.current
    if doc is not None:
        app.log_line(f"browsing {papis.commands.browse.run(doc)}")


@command("doc.edit_raw", "edit info.yaml in $EDITOR")
def doc_edit_raw(app: Any) -> None:
    """Full-screen `$EDITOR` on `info.yaml` — never a pty embedded in a pane."""
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
    except Exception as exc:
        app.log_line(f"[red]invalid YAML, nothing reloaded:[/] {exc}")
        app.log_line("[yellow]papis cannot load this document until it parses — press E again[/]")
        return
    _resync(app, doc)
    app.log_line(f"edited {doc.get('ref', doc.get('title', ''))}")


def _resync(app: Any, doc: Any) -> None:
    """Re-read a document papis itself wrote, and put it back in the index."""
    import papis.database

    doc.load()
    papis.database.get().update(doc)
    app.refresh_rows()


@command("doc.edit", "edit")
def doc_edit(app: Any) -> None:
    """`edit.mode` decides which editor. The structured one is not built (SPEC
    § Editing), so `editor` is the shipped default and this is `doc.edit_raw`
    with a note when the config still asks for the form."""
    if app.cfg.get("edit.mode", "editor") == "structured":
        app.log_line("[yellow]the structured editor is not built yet; opening $EDITOR[/]")
    doc_edit_raw(app)


@command("doc.notes", "notes")
def doc_notes(app: Any) -> None:
    """Open (creating if needed) the document's notes file in `$EDITOR`.

    `papis.commands.edit.edit_notes` creates the file from papis's own template
    and writes the `notes` key itself — one of the few writes that does not go
    through `safewrite`, because it is papis's own API doing it, exactly as with
    `papis.commands.add.run`.
    """
    import papis.commands.edit

    doc = app.current
    if doc is None:
        return
    with app.suspend():
        papis.commands.edit.edit_notes(doc, git=bool(app.cfg.papis("edit.use_git", "use-git")))
    _resync(app, doc)
    app.log_line(f"notes: {doc.get('notes', 'unchanged')}")


@command("doc.set", "set any field")
def doc_set(app: Any, key: str | None = None, value: str | None = None) -> None:
    """Set one field on every target, through the safe write. Batch-aware.

    With no `key` it prompts — `c f` and a bare `:doc.set` land in the same
    place. An empty value **removes** the key, which is the only way to clear a
    field without opening `$EDITOR`. The value's type comes from
    `library.typed`, so `tags` becomes a list and `year` an int.
    """
    if key is None:
        app.open_prompt("set", "key value  (no value clears the field)")
        return
    what = f"{key} = {value}" if value else f"remove {key}"
    _set_field(app, key, lambda doc: library.typed(key, value or "", doc.get(key)), what)


def _set_field(app: Any, key: str, value_of: Callable[[Any], Any], what: str) -> None:
    """Plan one value per target, confirm a batch, then write. The single path
    every `c` verb takes — they differ only in how the value is worked out.

    `value_of` returning `None` removes the key.
    """
    targets = app.targets
    if not targets:
        return
    try:  # every value first, so a bad number aborts before anything is written
        planned = [(doc, value_of(doc)) for doc in targets]
    except ValueError as exc:
        app.log_line(f"[red]{exc}[/]")
        return
    if len(targets) == 1:
        _set_apply(app, planned, key)
        return
    # SPEC: a batch confirms against the *total* marked count. The picker is the
    # confirm — ponytail: no preview list, the count and the value are the whole
    # question here, unlike a delete.
    items = [
        ui.Item(label=f"set {what} on {len(targets)} documents", value=True),
        ui.Item(label="cancel", value=False),
    ]
    ui.pick(app, items, title=f"{what} — {len(targets)} documents?")(
        lambda ok, _invert: _set_apply(app, planned, key) if ok else None
    )


def _write_field(app: Any, planned: list[tuple[Any, Any]], key: str) -> int:
    """Write one key across documents. Returns how many landed."""
    done = 0
    for doc, new in planned:

        def write(data: Any, new: Any = new) -> None:
            if new is None:
                data.pop(key, None)
            else:
                data[key] = new

        try:
            safewrite.edit(doc, write)
        except safewrite.StaleError:
            app.log_line(f"[yellow]{doc.get('ref', '')} changed on disk; press r to reload[/]")
        except Exception as exc:
            app.log_line(f"[red]{key} on {doc.get('ref', '')} failed:[/] {exc}")
        else:
            done += 1
    return done


def _resort(app: Any) -> None:
    """The edit may have been to the key the list is sorted on — `c f year` while
    sorted by year has to move the row, not just repaint it. `refresh_rows`
    keeps the cursor on the document, so it follows wherever it lands."""
    app.apply_sort()
    app.refilter()


def _set_apply(app: Any, planned: list[tuple[Any, Any]], key: str) -> None:
    """Write, then push the step that puts the old values back.

    SPEC: metadata edits get a session-local in-memory undo stack *regardless
    of strategy* — this is the half of undo that has nothing to do with the
    trash or with git. The previous values are read before the write, so an
    undo restores exactly what was there, including the key's absence.
    """
    before = [(doc, doc.get(key)) for doc, _ in planned]
    done = _write_field(app, planned, key)

    def restore() -> None:
        _write_field(app, before, key)
        _resort(app)

    def again() -> None:
        _write_field(app, planned, key)
        _resort(app)

    if done:
        app.history.push(undo.Step(f"set {key} on {done} document(s)", restore, again))
    _resort(app)
    app.log_line(f"set {key} on {done}/{len(planned)} document(s)")


def tags_of(doc: Any) -> list[str]:
    """A document's tags as a list, whatever papis found in the file — the key
    is declared `tags:list` but a hand-written `tags: ml, cv` is legal YAML and
    happens."""
    value = doc.get("tags") or []
    return list(value) if isinstance(value, list) else library.typed("tags", str(value)) or []


@command("doc.tag", "add tags")
def doc_tag(app: Any, tags: str | None = None) -> None:
    """Add tags, keeping the ones already there. Batch-aware.

    Adding rather than replacing is the whole difference from `doc.set tags`:
    marking twenty documents and tagging them must not wipe what each already
    had. Order is preserved so the file diff stays small.
    """
    if tags is None:
        app.open_prompt("tag", "tags to add")
        return
    wanted = library.typed("tags", tags) or []
    if not wanted:
        return

    def add(doc: Any) -> list[str]:
        current = tags_of(doc)
        return [*current, *(tag for tag in wanted if tag not in current)]

    _set_field(app, "tags", add, f"+{', '.join(wanted)}")


@command("doc.untag", "remove tags")
def doc_untag(app: Any, tags: str | None = None) -> None:
    """Remove tags. A document left with none loses the key rather than keeping
    an empty list, which is what `library.typed` does for every other field."""
    if tags is None:
        here = ", ".join(tags_of(app.current)) if app.current else ""
        app.open_prompt("untag", f"tags to remove{f' — has: {here}' if here else ''}")
        return
    wanted = library.typed("tags", tags) or []
    if not wanted:
        return

    def drop(doc: Any) -> list[str] | None:
        return [tag for tag in tags_of(doc) if tag not in wanted] or None

    _set_field(app, "tags", drop, f"-{', '.join(wanted)}")


# ponytail: SPEC's three values, not a config key. `:doc.status submitted` still
# works — the field is a free string, the picker is just the common case.
STATUSES = ("unread", "reading", "read")


@command("doc.status", "reading status")
def doc_status(app: Any, value: str | None = None) -> None:
    if value is None:
        items = [ui.Item(label=status, value=status) for status in STATUSES]
        items.append(ui.Item(label="clear", value="", hint="remove the field"))
        current = app.current.get("reading_status") if app.current else None
        ui.pick(app, items, title="Reading status", current=current)(lambda status, _invert: doc_status(app, status))
        return
    _set_field(app, "reading_status", lambda _doc: value or None, f"reading_status = {value or '-'}")


@command("doc.rating", "rating")
def doc_rating(app: Any, value: int | None = None) -> None:
    """0 to 5, where 0 removes the key. Written as an int: papis does not declare a
    type for `rating`, so `library.typed` would keep it as text."""
    if value is None:
        items = [ui.Item(label=str(n) if n else "0 — clear", value=n) for n in range(6)]
        current = app.current.get("rating") if app.current else None
        ui.pick(app, items, title="Rating", current=current)(lambda stars, _invert: doc_rating(app, stars))
        return
    stars = max(0, min(5, value))
    _set_field(app, "rating", lambda _doc: stars or None, f"rating = {stars or '-'}")


# ── add ─────────────────────────────────────────────────────────────────────


@command("doc.add", "add document")
def doc_add(app: Any, source: str | None = None) -> None:
    """Pick where the document comes from, confirm the metadata, then add it.

    With no `source` this lists every source papis can serve — a file, the inbox,
    a URL, and each registered importer — so a new papis plugin appears without a
    change here. `source` names one directly, which is how `i` reaches the inbox.
    """
    if source is None:
        _add_source_picker(app)
    elif source == "url":
        app.open_prompt("import:url", "a publisher URL")
    elif source == "bib":
        app.open_prompt("import:bib", "path to a .bib")
    elif source in fetch.SOURCES:
        app.open_prompt(f"import:{source}", fetch.SOURCES[source][1])
    else:
        _add_from_file(app, source)


def _add_source_picker(app: Any) -> None:
    inbox = app.cfg.as_path("files.inbox")
    items = [
        ui.Item(label="a file on disk…", value="", hint="path"),
        ui.Item(label=f"the inbox ({inbox})", value="inbox", hint="newest first"),
        ui.Item(label="a URL from any publisher…", value="url", hint="23 downloaders"),
        ui.Item(label="a .bib file…", value="bib", hint="choose an entry"),
    ]
    items += [
        ui.Item(label=f"{fetch.SOURCES[name][0]}…", value=name, hint=name)
        for name in fetch.available()
        if name != "bibtex"  # the .bib row above lists entries instead of taking the first
    ]
    ui.pick(app, items, title="Add from")(lambda source, _i: doc_add(app, source or "prompt"))


def _add_from_file(app: Any, source: str) -> None:
    if source == "prompt":
        app.open_prompt("add", "path to a file")
    elif source == "inbox":
        inbox = app.cfg.as_path("files.inbox")
        if inbox is None or not inbox.is_dir():
            app.log_line(f"[yellow]inbox not found:[/] {inbox}")
            return
        entries = sorted(
            (p for p in inbox.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        items = [ui.Item(label=p.name, value=str(p), hint=_age(p)) for p in entries]
        ui.pick(app, items, title=f"Add from {inbox}")(lambda path, _i: add_form(app, Path(path)))
    else:
        add_form(app, Path(source).expanduser())


def _age(path: Path) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def _expand(data: dict[str, str]) -> dict[str, Any]:
    """Form fields as papis keys: `author` also becomes `author_list`, which is
    what naming schemes sort and format on."""
    import papis.document

    out: dict[str, Any] = dict(data)
    if out.get("author"):
        out["author_list"] = papis.document.split_authors_name([out["author"]])
    if isinstance(out.get("tags"), str):
        out["tags"] = out["tags"].replace(",", " ").split()
    return out


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
    """
    if path is not None and not path.is_file():
        app.log_line(f"[red]no such file:[/] {path}")
        return

    rules = place.Rules.from_config(app.cfg)
    fetched = fetched or {}
    initial = {field: library.display(fetched.get(field, "")) for field in ui.ADD_FIELDS if fetched.get(field)}
    initial.setdefault("title", path.stem if path else "")

    def preview(data: dict[str, str]) -> str:
        """Where the file would end up. Pure: `place.target` touches nothing."""
        import papis.document

        if path is None:
            return "no file — metadata only"
        doc = papis.document.from_data({**_expand(data), "files": [path.name]})
        rule = rules.first_match(path)
        if rule.op == "in-place" or not rule.dest:
            return f"stays in the document folder ({rule.name})"
        try:
            dest = str(place.target(doc, path, rule, rules, default=""))
        except Exception as exc:
            return f"destination not resolvable yet ({exc})"
        # papis hands back the unformatted pattern when a key it needs is missing.
        if "{" in dest:
            return "fill in the fields above to see the destination"
        return f"-> {dest}"

    def confirm(data: dict[str, str] | None) -> None:
        if data is not None:
            extra = {k: v for k, v in fetched.items() if k in _KEEP_FETCHED and v}
            _add_document(app, path, data, extra)

    app.push_screen(ui.AddForm(path, initial, preview), confirm)


def _add_document(app: Any, path: Path | None, data: dict[str, str], extra: dict[str, Any] | None = None) -> None:
    import papis.commands.add

    # The form wins over the fetched record: the user just looked at both.
    data = {**(extra or {}), **_expand(data)}
    data.setdefault(library.TIME_ADDED, library.stamp())
    try:
        # ponytail: papis copies the source in; the original stays in the inbox
        # rather than being deleted behind the user's back. An empty path list is
        # a metadata-only document, which papis accepts.
        papis.commands.add.run(
            [str(path)] if path else [],
            data=dict(data),
            auto_doctor=app.cfg.get("add.auto_doctor", False),
            git=bool(app.cfg.papis("edit.use_git", "use-git")),
        )
    except Exception as exc:
        app.log_line(f"[red]add failed:[/] {exc}")
        return

    reload(app)
    added = next((d for d in app.docs if d.get("title") == data.get("title")), None)
    if added is not None:
        app.refresh_rows(keep=library.doc_id(added))
        if path is not None:
            files_relocate(app)
    where = f" (source left at {path})" if path else " (no file)"
    app.log_line(f"added {data.get('title', path.name if path else '?')}{where}")


# ── export ──────────────────────────────────────────────────────────────────


def _yank(app: Any, text: str, what: str) -> None:
    if not text:
        app.log_line(f"[yellow]nothing to yank for {what}[/]")
        return
    method = clip.copy(app, text)
    app.log_line(f"yanked {what} ({method})")


@command("export.citekey", "\\cite{ref}")
def export_citekey(app: Any) -> None:
    import papis.format

    fmt = app.cfg.get("export.citekey_format", "{doc[ref]}")
    # Verbatim stored values: LaTeX in a title is display-stripped, never yanked stripped.
    keys = [papis.format.format(fmt, doc, default="") for doc in app.targets]
    _yank(app, " ".join(k for k in keys if k), f"{len(keys)} citekey(s)")


@command("export.path", "absolute path")
def export_path(app: Any) -> None:
    paths = [str(p) for doc in app.targets for p in files_of(app, doc)[:1]]
    _yank(app, "\n".join(paths), "path")


@command("export.url", "DOI/URL")
def export_url(app: Any) -> None:
    urls = []
    for doc in app.targets:
        doi = doc.get("doi")
        urls.append(doc.get("url") or (f"https://doi.org/{doi}" if doi else ""))
    _yank(app, "\n".join(u for u in urls if u), "url")


@command("export.bibtex", "bibtex")
def export_bibtex(app: Any, target: str | None = None) -> None:
    import papis.bibtex

    entries = [papis.bibtex.to_bibtex(doc) for doc in app.targets]
    text = "\n".join(e for e in entries if e)
    if target:
        Path(target).expanduser().write_text(text)
        app.log_line(f"wrote {len(entries)} entries to {target}")
    else:
        _yank(app, text, f"{len(entries)} bibtex entrie(s)")


# ── files ───────────────────────────────────────────────────────────────────


@command("files.relocate", "relocate + rename to scheme")
def files_relocate(app: Any, force: bool = False) -> None:
    """Run `place()` over every file of every target. Partial failure is normal:
    the clean ones go through, the rest are reported in the log."""
    rules = place.Rules.from_config(app.cfg)
    tally: dict[str, int] = {}
    for doc in app.targets:
        entries = list(doc.get("files", []))
        results = [place.place(doc, e, rules, force=force, previous=e) for e in entries]
        for result in results:
            tally[result.status] = tally.get(result.status, 0) + 1
            if result.status not in ("ok", "already", "unmanaged"):
                app.log_line(f"[yellow]{result.status}[/] {result.src.name}: {result.message}")

        updated = [
            r.entry if r.entry and r.status in ("ok", "already") else e for e, r in zip(entries, results, strict=True)
        ]
        if updated == entries:
            continue
        try:
            safewrite.edit(doc, lambda data, new=updated: data.__setitem__("files", new))
        except Exception as exc:
            # File first, info.yaml second — so a failed write means rolling the
            # files back, not leaving dangling references behind.
            for result in results:
                place.rollback(result)
            app.log_line(f"[red]rolled back {doc.get('ref', '')}:[/] {exc}")

    app.refresh_rows()
    summary = ", ".join(f"{count} {status}" for status, count in sorted(tally.items()))
    app.log_line(f"relocate: {summary or 'nothing to do'}")
    if tally.get("conflict") or tally.get("error"):
        app.query_one("#log-pane").display = True


# ── app ─────────────────────────────────────────────────────────────────────


@command("app.escape", "cancel")
def app_escape(app: Any) -> None:
    """Resolve per `escape_chain`. Never clears marks — only `m c` does."""
    for step in app.km.option("escape_chain", ["modal", "narrow"]):
        if step == "modal" and app.prompt_kind:
            app.close_prompt()
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
    """
    items = []
    for binding in sorted(app.km.modes.get(app.mode, {}).values(), key=lambda b: (b.chord[0].casefold(), b.keys)):
        args = " ".join(str(value) for value in binding.args.values())
        note = "" if binding.cmd in REGISTRY else "  (not implemented)"
        label = f"{binding.keys:<10} {binding.desc or binding.cmd}{f' — {args}' if args else ''}"
        hint = "" if binding.desc in ("", binding.cmd) else binding.cmd  # no echo of the label
        items.append(ui.Item(label=label + note, value=binding.cmd, hint=hint))
    if app.mode != "list":
        # Guaranteed by the dispatcher, so it is in no mode's table.
        items = [ui.Item(label=f"{'escape':<10} back to the list", value=""), *items]
    app.push_screen(ui.SelectList(items, title=f"Keys — {app.mode} mode"))


@command("cmdline.open", "run a command by name")
def cmdline_open(app: Any) -> None:
    """Discoverability layer 4: fuzzy completion over command names, each with
    its binding beside it — the layer that teaches the keymap.

    It lists the *registry*, not the keymap: unlike `help.show` this runs what
    you pick, so a command nobody has bound is still reachable, and one that is
    bound teaches its keys on the way past. The keys come from the current mode,
    falling back to the list mode's table so a pane with no bindings of its own
    still shows where the command lives.
    """
    show_keys = app.km.option("show_keys_in_cmdline", True)

    def keys(name: str) -> str:
        if not show_keys:
            return ""
        return app.km.for_command(app.mode, name) or app.km.for_command("list", name) or ""

    items = [
        ui.Item(
            label=f"{name:<20} {cmd.desc}",
            value=name,
            hint=keys(name),
            haystack=commands.signature(name),
        )
        for name, cmd in sorted(REGISTRY.items())
    ]
    ui.pick(app, items, title="Run command")(lambda name, _invert: cmdline_args(app, name))


def cmdline_args(app: Any, name: str) -> None:
    """Ask for the arguments, or run straight away when there are none.

    Arguments are what the command line is *for*: everything else already has a
    key. A command with parameters therefore always gets the prompt, optional
    ones included — `enter` on an empty line keeps every default, which is what
    a binding with no `args` does.
    """
    if not commands.params(name):
        app.run_command(name)
        return
    app.open_prompt(f"cmdline:{name}", f"{name} {commands.signature(name)}")


def cmdline_run(app: Any, name: str, text: str) -> None:
    try:
        args = commands.parse_args(name, text)
    except ValueError as exc:  # too many arguments, an unclosed quote, a bad number
        app.log_line(f"[red]{exc}[/]")
        return
    app.log_line(f"[dim]:{name}{' ' + text if text.strip() else ''}[/]")
    app.run_command(name, args)


@command("theme.picker", "theme…")
def theme_picker(app: Any, name: str | None = None) -> None:
    """Switch palette, live. The themes are Textual's own — 21 of them, light
    and dark — so there is no palette file here to rot against a release.

    The choice lasts the session: `[ui] theme` in `config.toml` is what makes it
    stick, and the log says so rather than writing to the user's config behind
    their back.
    """
    if name is not None:
        app.apply_theme(name)
        return
    items = [
        ui.Item(label=theme, value=theme, hint="light" if not app.available_themes[theme].dark else "")
        for theme in sorted(app.available_themes)
    ]

    def apply(theme: str, _invert: bool) -> None:
        app.apply_theme(theme)
        app.refresh_rows()  # the marked-row colour is a Rich style, not CSS
        app.log_line(f'theme: {theme} — keep it with [bold]theme = "{theme}"[/] under [ui]')

    ui.pick(app, items, title="Theme", current=app.theme)(apply)


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


# ── merge ───────────────────────────────────────────────────────────────────


@command("doc.merge", "merge marked documents")
def doc_merge(app: Any) -> None:
    """Fold the marked documents into one.

    The `ref` you keep is the document you keep: it holds the folder and the
    `papis_id`, and the others' folders go to the trash. Keys only the others had
    are filled in silently; a key two records genuinely disagree on is asked
    about, one question at a time.
    """
    docs = [d for d in app.docs if library.doc_id(d) in app.marks]
    if len(docs) < 2:
        app.log_line("[yellow]merge needs at least two marked documents[/]")
        return

    choices = merge.survivor_choices(docs)
    if len(choices) == 1:
        _merge_conflicts(app, choices[0][1], [d for d in docs if d is not choices[0][1]])
        return

    items = [
        ui.Item(
            label=ref,
            value=index,
            hint=f"{doc.get('type', '')} {len(doc.get('files') or [])} file(s)".strip(),
            haystack=library.display(doc.get("title", "")),
        )
        for index, (ref, doc) in enumerate(choices)
    ]

    def picked(index: int, _invert: bool) -> None:
        survivor = choices[index][1]
        _merge_conflicts(app, survivor, [d for d in docs if d is not survivor])

    ui.pick(app, items, title=f"Merge {len(docs)} documents — which ref survives?")(picked)


def _merge_conflicts(app: Any, survivor: Any, others: list[Any]) -> None:
    """Ask about each genuine clash, then confirm. Recursive: one modal per key."""
    plan = merge.plan(survivor, others)
    group = [survivor, *others]
    resolved: dict[str, Any] = {}

    def ask(remaining: list[str]) -> None:
        if not remaining:
            _merge_apply(app, plan, resolved)
            return
        key, rest = remaining[0], remaining[1:]
        values = plan.clashes[key]
        items = [
            ui.Item(label=library.display(value), value=("value", index), hint=key)
            for index, value in enumerate(values)
        ]
        # the shortcut: stop being asked and take this document's side throughout
        items += [
            ui.Item(
                label=f"keep everything else from {doc.get('ref') or doc.get('title', '?')}",
                value=("all", position),
                hint="resolves the rest",
            )
            for position, doc in enumerate(group)
        ]

        def chose(choice: tuple[str, int], _invert: bool) -> None:
            kind, index = choice
            if kind == "value":
                resolved[key] = values[index]
                ask(rest)
                return
            winner = group[index]
            for pending in [key, *rest]:
                if winner.get(pending) not in (None, "", [], {}):
                    resolved[pending] = winner.get(pending)
            _merge_apply(app, plan, resolved)

        ui.pick(app, items, title=f"{key} — {len(remaining)} left")(chose)

    ask(plan.questions)


def _merge_apply(app: Any, plan: Any, resolved: dict[str, Any]) -> None:
    data = merge.resolve(plan, resolved)
    rules = place.Rules.from_config(app.cfg)
    trash_dir = app.cfg.as_path("undo.trash_dir") or Path.home() / ".local/share/ptui/trash"

    # Files first, and resolved against the folder they still live in: an entry
    # may be relative to a folder that is about to be trashed.
    entries = list(plan.survivor.get("files") or [])
    for doc in plan.others:
        for entry in doc.get("files") or []:
            source = place.resolve(doc, entry)
            if not source.exists():
                app.log_line(f"[yellow]missing file, not merged:[/] {entry}")
                continue
            result = place.place(plan.survivor, source, rules, previous=None)
            if result.entry and result.entry not in entries:
                entries.append(result.entry)
            elif result.status not in ("ok", "already"):
                app.log_line(f"[yellow]{result.status}[/] {source.name}: {result.message}")
    if entries != list(plan.survivor.get("files") or []):
        data["files"] = entries

    try:
        safewrite.edit(plan.survivor, lambda info: info.update(data))
    except safewrite.StaleError:
        app.log_line("[yellow]info.yaml changed on disk; press r to reload[/]")
        return
    except Exception as exc:
        app.log_line(f"[red]merge failed:[/] {exc}")
        return

    # Moving the folder is not enough: papis keeps its own index, and a document
    # whose folder has gone still comes back from the cache. `papis rm` pairs the
    # two, so this does too.
    import papis.database

    database = papis.database.get(app.cfg.get("general.library") or None)
    trashed = []
    for doc in plan.others:
        folder = doc.get_main_folder()
        if not folder:
            continue
        try:
            trashed.append(place.trash(Path(folder), trash_dir))
        except Exception as exc:
            app.log_line(f"[red]could not trash {folder}:[/] {exc}")
            continue
        try:
            database.delete(doc)
        except Exception as exc:
            app.log_line(f"[yellow]folder trashed but the index kept it:[/] {exc}")

    app.marks.clear()
    reload(app)
    app.log_line(
        f"merged {len(plan.others) + 1} -> {plan.survivor.get('ref', '')}: "
        f"{len(data)} field(s), {len(entries)} file(s), {len(trashed)} folder(s) trashed"
    )
    for folder in trashed:
        app.log_line(f"[dim]trashed {folder}[/]")
    app.query_one("#log-pane").display = True


# ── doctor ──────────────────────────────────────────────────────────────────


def _checks(app: Any, checks: str = "") -> list[str]:
    configured = checks.split() if checks else app.cfg.get("doctor.checks", [])
    for name in doctor.unknown_checks(configured):
        app.log_line(f"[yellow]doctor: no such check[/] {name}")
    return configured


def _doctor_targets(app: Any, current: bool) -> list[Any]:
    """`current = true` means this document only, ignoring marks — the difference
    between `! d` and `! !`. Without it the whole narrowed set is the target,
    marks first: doctoring only the document under the cursor is what `! d` is
    for, and a library-wide check needs a library-sized set to say anything.
    """
    if not current:
        return app.targets if app.marks else app.rows
    doc = app.current
    return [doc] if doc is not None else []


def _findings(app: Any, current: bool = False) -> list[tuple[Any, doctor.Finding]]:
    """Every finding over the target set, freshly scanned. Read-only."""
    checks = _checks(app)
    return [(doc, f) for doc in _doctor_targets(app, current) for f in doctor.scan(doc, checks)]


def _label(doc: Any, finding: doctor.Finding) -> str:
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
    """
    names = _checks(app, checks)
    targets = _doctor_targets(app, current)
    found = [(doc, f) for doc in targets for f in doctor.scan(doc, names)]
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
    app.doctor_only = True
    app.refilter()


@command("doctor.fix_pick", "fix one finding…")
def doctor_fix_pick(app: Any) -> None:
    """Choose one of this document's findings to fix.

    A picker used as a picker: reporting is the info pane's job, and this asks
    *which one*. Nothing is written by opening it.
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
        _apply_one(app, *found[index])

    ui.pick(app, items, title="Fix which finding?")(apply)


def _apply_one(app: Any, doc: Any, finding: doctor.Finding) -> bool:
    if finding.fix_action is None:
        app.log_line(f"[yellow]no automatic fix for[/] {finding.name}: {finding.msg}")
        if finding.suggestion_cmd:
            app.log_line(f"[dim]papis suggests:[/] {finding.suggestion_cmd}")
        return False
    try:
        changed = doctor.fix(doc, finding)
    except safewrite.StaleError:
        app.log_line("[yellow]info.yaml changed on disk; press r to reload[/]")
        return False
    except Exception as exc:
        app.log_line(f"[red]fix failed:[/] {exc}")
        return False
    app.log_line(f"fixed {finding.name} on {doc.get('ref', '')}: {', '.join(changed) or 'no change'}")
    doctor.scan(doc, _checks(app))  # the write invalidated the cache; the pane reads it
    app.refresh_rows()
    return True


@command("doctor.fix", "fix doctor findings")
def doctor_fix(app: Any, current: bool = False) -> None:
    """Apply every fixable finding over the target set.

    A finding cannot travel through a `keys.toml` argument, so the command
    reachable by name is the batch one — the same shape as every other verb
    here. `doctor.fix_pick` is the one-finding path.
    """
    found = _findings(app, current)
    fixable = [(doc, f) for doc, f in found if f.fix_action]
    for doc, finding in fixable:
        _apply_one(app, doc, finding)
    skipped = len(found) - len(fixable)
    app.log_line(
        f"doctor: fixed {len(fixable)}, {skipped} with no automatic fix" if found else "doctor: nothing to fix"
    )
    if skipped:
        app.query_one("#log-pane").display = True


# ── delete and undo ─────────────────────────────────────────────────────────


def trash_dir(app: Any) -> Path:
    return app.cfg.as_path("undo.trash_dir") or Path.home() / ".local/share/ptui/trash"


def library_dir(app: Any) -> Path:
    """Where the current library lives on disk, straight from papis."""
    import papis.config

    name = app.cfg.get("general.library") or papis.config.get_lib_name()
    return Path(papis.config.get_lib_from_name(name).paths[0])


@dataclass(frozen=True, slots=True)
class Doomed:
    """One file a delete would take with it, and why it is offered or not."""

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
    document points at the same realpath: with a shared `pdf_root` and a script
    appending entries, two documents on one file is a realistic accident.
    """
    root = app.cfg.as_path("files.pdf_root")
    doomed = []
    for doc in targets:
        folder = Path(doc.get_main_folder() or ".")
        for entry in doc.get("files") or []:
            path = place.resolve(doc, entry)
            if folder in path.parents:
                continue
            managed = root is not None and root in path.parents
            others = [
                other.get("ref") or library.doc_id(other)
                for other in app.docs
                if other is not doc and any(place.resolve(other, e) == path for e in other.get("files") or [])
            ]
            note = "" if managed else "outside the managed roots"
            if others:
                note = f"also in {', '.join(others[:3])}"
            doomed.append(Doomed(doc, entry, path, ui.FileChoice(str(path), managed and not others, note)))
    return doomed


@command("doc.delete", "delete document(s)")
def doc_delete(app: Any, force: bool = False) -> None:
    """Remove documents from the library, recoverably.

    Confirms against the **total** number of targets with a preview of what
    they are — marking 200, narrowing to 3 and pressing `d d` must not delete
    200 on the strength of what is on screen. `force` skips the dialog and
    keeps no files, which is the batch-script shape.
    """
    targets = app.targets
    if not targets:
        return
    doomed = _doomed_files(app, targets)
    if force:
        _delete_apply(app, targets, [d for d in doomed if d.choice.checked])
        return

    shown = [f"  {doc.get('ref') or library.doc_id(doc)}" for doc in targets[:10]]
    if len(targets) > 10:
        shown.append(f"  [dim]… and {len(targets) - 10} more[/]")
    inside = sum(len(doc.get("files") or []) for doc in targets) - len(doomed)
    if inside:
        shown.append(f"[dim]{inside} file(s) inside the folders go with them[/]")

    def confirm(chosen: list[int] | None) -> None:
        if chosen is not None:
            _delete_apply(app, targets, [doomed[index] for index in chosen])

    app.push_screen(
        ui.ConfirmDelete(
            shown,
            [d.choice for d in doomed],
            title=f"Delete {len(targets)} document(s)",
        ),
        confirm,
    )


def _delete_apply(app: Any, targets: list[Any], files: list[Doomed]) -> None:
    """Trash the chosen files, remove the folders per `undo.strategy`, and push
    one undo step for the whole operation."""
    import papis.database

    strategy = app.cfg.get("undo.strategy", "trash")
    where = trash_dir(app)
    moved: list[tuple[Path, Path]] = []
    for doomed in files:
        try:
            moved.append((doomed.path, place.trash(doomed.path, where)))
        except Exception as exc:
            app.log_line(f"[red]could not trash {doomed.path.name}:[/] {exc}")

    folders = [Path(doc.get_main_folder()) for doc in targets if doc.get_main_folder()]
    label = f"delete {len(targets)} document(s)"
    step: undo.Step | None = None
    try:
        if strategy == "git":
            step = _delete_with_git(app, folders, label, moved)
        else:
            for folder in folders:
                moved.append((folder, place.trash(folder, where)))
            step = undo.Step(label, lambda: _undo_trash(app, moved), lambda: doc_delete(app, True))
    except Exception as exc:
        app.log_line(f"[red]delete failed:[/] {exc}")
        return

    # Papis keeps its own index, and a document whose folder has gone still
    # comes back from the cache — the same pairing `papis rm` does.
    database = papis.database.get(app.cfg.get("general.library") or None)
    for doc in targets:
        try:
            database.delete(doc)
        except Exception as exc:
            app.log_line(f"[yellow]folder gone but the index kept it:[/] {exc}")

    if strategy == "none":
        app.history.clear()  # nothing here is undoable; do not offer the last one
    elif step is not None:
        app.history.push(step)

    app.marks.clear()
    reload(app)
    app.log_line(
        f"deleted {len(targets)} document(s), {len(files)} file(s)" + ("" if strategy == "none" else " — u to undo")
    )
    app.query_one("#log-pane").display = True


def _delete_with_git(app: Any, folders: list[Path], label: str, moved: list[tuple[Path, Path]]) -> undo.Step:
    """`git rm` the folders in one commit, and undo by reverting it.

    One ptui operation is one commit, so the log reads as a history of what the
    user did. Files outside the repo were already trashed by the caller, and
    their undo rides along with the revert.
    """
    root = undo.git_root(folders[0]) if folders else None
    if root is None:
        raise RuntimeError("undo.strategy = git, but the library is not a git repository")
    commit = undo.git_delete(root, folders, label)

    def back() -> None:
        undo.git_revert(root, commit)
        undo.restore(moved)
        reload(app, rescan=True)
        app.log_line(f"reverted {commit[:8]}")

    return undo.Step(label, back, lambda: doc_delete(app, True))


def _undo_trash(app: Any, moved: list[tuple[Path, Path]]) -> None:
    restored = undo.restore(moved)
    reload(app, rescan=True)
    app.log_line(f"restored {len(restored)} path(s) from the trash")


@command("app.undo", "undo")
def app_undo(app: Any) -> None:
    step = app.history.undo()
    if step is None:
        app.log_line("[yellow]nothing to undo[/]")
        return
    app.refresh_rows()
    app.log_line(f"undo: {step.label}")


@command("app.redo", "redo")
def app_redo(app: Any) -> None:
    step = app.history.redo()
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
    """
    if app.cfg.get("undo.strategy") != "git" or not app.cfg.get("undo.git_warn_untracked", True):
        return
    root = undo.git_root(library_dir(app))
    if root is None:
        app.log_line("[yellow]undo.strategy = git, but the library is not a git repository[/]")
        return
    pdf_root = app.cfg.as_path("files.pdf_root")
    if pdf_root and not undo.git_tracks(root, pdf_root):
        app.log_line(f"[yellow]git does not track {pdf_root}; those files undo via the trash[/]")


@command("app.quit", "quit")
def app_quit(app: Any) -> None:
    app.exit()


# Modal-only: `SelectList` reads these straight out of `[modes.picker]`. They are
# registered so the keymap check does not flag them as unknown.
@command("picker.confirm", "confirm")
def picker_confirm(app: Any, invert: bool = False) -> None:
    app.log_line("[dim]picker.confirm only applies inside a picker[/]")


@command("picker.cancel", "cancel")
def picker_cancel(app: Any) -> None:
    app.log_line("[dim]picker.cancel only applies inside a picker[/]")
