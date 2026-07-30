"""Command implementations. Every one is registered in `commands.REGISTRY`.

Handlers take the app first and are plain functions — nothing here imports the
app module, so `app.py` can import this at load time to register everything.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from textual.widgets import DataTable

from ptui import clip, doctor, library, place, safewrite, ui
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
    pane = app.query_one("#log-pane")
    pane.display = not pane.display
    app.focus_pane("log" if pane.display else "list")


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
    else:
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


@command("sort.picker", "sort by…")
def sort_picker(app: Any) -> None:
    """Presets first, then keys discovered in the library. `enter` applies the
    key's own default direction, `shift+enter` inverts it — a single global
    reverse flag is wrong half the time (author ascending, date descending)."""
    presets = app.cfg.get("list.sort_presets", [])
    items = [
        ui.Item(
            label=f"{p.get('label', p['key'])} "
            f"{ui.glyph('sort_desc' if p.get('dir') == 'desc' else 'sort_asc')}",
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
    items = [
        ui.Item(label=p.name, value=i, hint="" if p.exists() else "missing")
        for i, p in enumerate(paths)
    ]
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
    import papis.database

    doc = app.current
    if doc is None:
        return
    with app.suspend():
        papis.commands.edit.run(doc, wait=True)
    doc.load()
    papis.database.get().update(doc)
    app.refresh_rows()
    app.log_line(f"edited {doc.get('ref', doc.get('title', ''))}")


# ── add ─────────────────────────────────────────────────────────────────────


@command("doc.add", "add document")
def doc_add(app: Any, source: str | None = None) -> None:
    """Pick a source file, confirm the metadata, preview where the file lands."""
    if source == "inbox":
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
    elif source:
        add_form(app, Path(source).expanduser())
    else:
        app.open_prompt("add", "path to a file")


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


def add_form(app: Any, path: Path) -> None:
    if not path.is_file():
        app.log_line(f"[red]no such file:[/] {path}")
        return

    rules = place.Rules.from_config(app.cfg)

    def preview(data: dict[str, str]) -> str:
        """Where the file would end up. Pure: `place.target` touches nothing."""
        import papis.document

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
            _add_document(app, path, data)

    app.push_screen(ui.AddForm(path, {"title": path.stem}, preview), confirm)


def _add_document(app: Any, path: Path, data: dict[str, str]) -> None:
    import papis.commands.add

    data = _expand(data)
    try:
        # ponytail: papis copies the source in; the original stays in the inbox
        # rather than being deleted behind the user's back.
        papis.commands.add.run(
            [str(path)],
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
        files_relocate(app)
    app.log_line(f"added {data.get('title', path.name)} (source left at {path})")


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
            r.entry if r.entry and r.status in ("ok", "already") else e
            for e, r in zip(entries, results, strict=True)
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
        if step == "narrow" and app.narrow_query:
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
    for binding in sorted(
        app.km.modes.get(app.mode, {}).values(), key=lambda b: (b.chord[0].casefold(), b.keys)
    ):
        args = " ".join(str(value) for value in binding.args.values())
        note = "" if binding.cmd in REGISTRY else "  (not implemented)"
        label = f"{binding.keys:<10} {binding.desc or binding.cmd}{f' — {args}' if args else ''}"
        hint = "" if binding.desc in ("", binding.cmd) else binding.cmd  # no echo of the label
        items.append(ui.Item(label=label + note, value=binding.cmd, hint=hint))
    if app.mode != "list":
        # Guaranteed by the dispatcher, so it is in no mode's table.
        items = [ui.Item(label=f"{'escape':<10} back to the list", value=""), *items]
    app.push_screen(ui.SelectList(items, title=f"Keys — {app.mode} mode"))


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


# ── doctor ──────────────────────────────────────────────────────────────────


def _checks(app: Any) -> list[str]:
    configured = app.cfg.get("doctor.checks", [])
    for name in doctor.unknown_checks(configured):
        app.log_line(f"[yellow]doctor: no such check[/] {name}")
    return doctor.check_names(configured)


def _findings(app: Any) -> list[tuple[Any, doctor.Finding]]:
    """Every finding over the marked set, or the cursor. Read-only."""
    checks = _checks(app)
    return [(doc, f) for doc in app.targets for f in doctor.findings(doc, checks)]


def _label(doc: Any, finding: doctor.Finding) -> str:
    ref = doc.get("ref") or library.doc_id(doc)
    fixable = "" if finding.fix_action else "  (no automatic fix)"
    return f"{ref}  {finding.msg}{fixable}"


@command("doctor.run", "run doctor")
def doctor_run(app: Any, checks: str = "") -> None:
    """Report findings. **Never fixes** — `doctor.fix` does that, on request.

    Batch-aware: every mark, or the cursor. `checks` is a space-separated
    override for `[doctor] checks`.
    """
    names = checks.split() if checks else app.cfg.get("doctor.checks", [])
    for name in doctor.unknown_checks(names):
        app.log_line(f"[yellow]doctor: no such check[/] {name}")
    targets = app.targets
    found = [(doc, f) for doc in targets for f in doctor.findings(doc, doctor.check_names(names))]
    for doc, finding in found:
        app.log_line(f"[yellow]{finding.name}[/] {_label(doc, finding)}")
    app.log_line(f"doctor: {len(found)} finding(s) over {len(targets)} document(s)")
    app.query_one("#log-pane").display = True


@command("view.doctor", "doctor findings")
def view_doctor(app: Any) -> None:
    """Browse findings; `enter` applies that one finding's fix, through safe-write.

    Nothing is written by opening this — the same rule as `help.show`, except
    that here confirming a row is an explicit request to change the document.
    """
    found = _findings(app)
    if not found:
        app.log_line(f"doctor: nothing to report over {len(app.targets)} document(s)")
        return
    items = [
        ui.Item(label=_label(doc, finding), value=index, hint=finding.name)
        for index, (doc, finding) in enumerate(found)
    ]

    def apply(index: int, _invert: bool) -> None:
        _apply_one(app, *found[index])

    ui.pick(app, items, title="Doctor findings")(apply)


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
    app.log_line(
        f"fixed {finding.name} on {doc.get('ref', '')}: {', '.join(changed) or 'no change'}"
    )
    app.refresh_rows()
    return True


@command("doctor.fix", "fix doctor findings")
def doctor_fix(app: Any) -> None:
    """Apply every fixable finding over the target set.

    SPEC describes this as "one selected finding", which is what `view.doctor`
    does on `enter`. A finding cannot travel through a `keys.toml` argument, so
    the command reachable by name is the batch one — the same shape as every
    other verb here.
    """
    found = _findings(app)
    fixable = [(doc, f) for doc, f in found if f.fix_action]
    for doc, finding in fixable:
        _apply_one(app, doc, finding)
    skipped = len(found) - len(fixable)
    app.log_line(
        f"doctor: fixed {len(fixable)}, {skipped} with no automatic fix"
        if found
        else "doctor: nothing to fix"
    )
    if skipped:
        app.query_one("#log-pane").display = True


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
