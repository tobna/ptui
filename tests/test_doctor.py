"""The papis doctor bridge: findings, the cache, fixes, and the doctor view."""

import os
import re

import papis.document
import pytest

from ptui import doctor, safewrite


def test_check_names_defaults_to_every_registered_check():
    """Check names defaults to every registered check."""
    assert doctor.check_names([]) == doctor.check_names(None)
    assert "refs" in doctor.check_names([])
    assert doctor.check_names(["refs", "nope"]) == ["refs"]  # unknown names dropped
    assert doctor.unknown_checks(["refs", "nope"]) == ["nope"]
    assert doctor.check_names(["key-type"]) == ["field-type"]  # papis's rename still works
    assert doctor.unknown_checks(["key-type"]) == []


def test_library_wide_checks_are_kept_out_of_the_per_document_set():
    """Library wide checks are kept out of the per document set."""
    per_doc, wide = doctor.check_names([]), doctor.check_names([], library_wide=True)
    assert wide == ["duplicated-keys"]
    assert set(per_doc) & set(wide) == set()
    assert "duplicated-values" in per_doc  # per-document despite the name


def test_looking_twice_at_a_document_finds_the_same_thing():
    """Looking twice at a document finds the same thing.

    `duplicated-keys` accumulates seen values in papis's
    module state, so it used to invent findings on the second look.
    """
    doc = papis.document.from_data({"title": "x", "type": "article", "ref": "ok", "doi": "10.1/a"})
    runs = [len(doctor.findings(doc)) for _ in range(4)]
    assert len(set(runs)) == 1


def test_library_wide_scan_is_repeatable_and_reports_the_second_document():
    """Library wide scan is repeatable and reports the second document."""
    docs = [  # `doctor-duplicated-keys-keys` defaults to ["ref"]
        papis.document.from_data({"title": "a", "ref": "same"}),
        papis.document.from_data({"title": "b", "ref": "same"}),
    ]
    found = doctor.scan_library(docs)
    assert [finding.name for _doc, finding in found] == ["duplicated-keys"]
    assert found[0][0]["title"] == "b"  # the duplicate is the second one to claim it
    assert len(doctor.scan_library(docs)) == 1  # state reset, so a re-run is identical


def test_a_written_document_is_rechecked_on_the_next_look(tmp_path, papis_lib):
    """A written document is rechecked on the next look.

    Not "not checked": one document is ~2 ms, so a stale entry is re-scanned
    with the checks it was scanned with, and an edit shows its effect at once.

    Args:
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, _info = broken_doc(tmp_path)
    assert doctor.cached(doc) is None  # never scanned: unknown, not clean
    found = doctor.scan(doc, ["refs"])
    assert doctor.cached(doc) == found
    assert [finding.name for finding in found] == ["refs"]

    doctor.fix(doc, found[0])
    assert doctor.cached(doc) == []  # re-checked against the written file
    assert doctor.CACHE[doc["papis_id"]][2] == ["refs"]  # with the same checks


def test_peek_keeps_the_old_findings_and_queues_the_document(tmp_path, papis_lib):
    """Peek keeps the old findings and queues the document.

    Args:
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = broken_doc(tmp_path)
    found = doctor.scan(doc, ["refs"])
    info.write_text(info.read_text().replace("ref: bad ref!", "ref: fine"))
    stat = info.stat()  # a distinct mtime, whatever the clock granularity
    os.utime(info, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    doc.load()
    assert doctor.peek(doc) == found  # still what the scan said: nothing ran
    assert list(doctor.STALE) == [doc["papis_id"]]
    assert doctor.recheck() == 1
    assert doctor.peek(doc) == []
    assert not doctor.STALE


async def test_a_redraw_rechecks_a_stale_document_off_the_main_thread(app, papis_lib, monkeypatch):
    """A `git pull` that touches every document must not freeze the next redraw.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        monkeypatch: pytest's monkeypatch fixture.
    """
    import threading

    from ptui import actions

    info = papis_lib / "lib" / "doc0" / "info.yaml"
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        only_refs(app)
        actions.doctor_run(app, current=True)  # doc0 is clean
        assert doctor.peek(app.current) == []
        info.write_text(info.read_text().replace("ref: Vaswani2017", "ref: bad ref!"))
        stat = info.stat()
        os.utime(info, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        app.current.load()

        scanned_on = []  # `cached` is the blocking read: whichever thread calls it runs the checks
        real = doctor.cached
        monkeypatch.setattr(doctor, "cached", lambda doc: scanned_on.append(threading.get_ident()) or real(doc))
        app.refresh_rows()
        assert "refs" not in str(app.query_one("#info").render())  # the old findings, for now
        await app.workers.wait_for_complete()
        await pilot.pause(0.2)
        assert scanned_on and threading.get_ident() not in scanned_on
        assert "refs" in str(app.query_one("#info").render())


def test_a_scan_with_named_checks_replaces_only_their_findings(tmp_path, papis_lib):
    """A scan with named checks replaces only their findings.

    Args:
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = broken_doc(tmp_path)  # a bad ref, and a year stored as text
    assert sorted(finding.name for finding in doctor.scan(doc, ["refs", "field-type"])) == ["field-type", "refs"]
    assert [finding.name for finding in doctor.scan(doc, ["refs"])] == ["refs"]  # returns what it ran
    assert [finding.name for finding in doctor.cached(doc)] == ["field-type", "refs"]  # keeps the rest
    assert doctor.CACHE[doc["papis_id"]][2] == ["field-type", "refs"]

    # written since: the other check runs again too, or the new stamp would vouch for it
    info.write_text(info.read_text().replace("year: '2024'", "year: 2024"))
    stat = info.stat()
    os.utime(info, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    doc.load()
    doctor.scan(doc, ["refs"])
    assert [finding.name for finding in doctor.cached(doc)] == ["refs"]


async def test_a_named_doctor_run_narrows_by_its_check_and_keeps_the_other_markers(app, tmp_path, papis_lib):
    """A named doctor run narrows by its check and keeps the other markers.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    from ptui import actions, library

    broken_doc(tmp_path)  # refs and field-type
    info = papis_lib / "lib" / "doc1" / "info.yaml"
    info.write_text(info.read_text().replace("year: 2016", "year: '2016'"))  # field-type only
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app.config.data.setdefault("doctor", {})["checks"] = ["refs", "field-type"]
        actions.reload(app)
        actions.doctor_run(app)
        assert sorted(library.doc_id(doc) for doc in app.rows) == ["id-broken", "id1"]

        actions.cmdline_run(app, "doctor.run", "refs")
        assert [library.doc_id(doc) for doc in app.rows] == ["id-broken"]  # what refs found
        doc1 = next(doc for doc in app.docs if library.doc_id(doc) == "id1")
        assert [finding.name for finding in doctor.cached(doc1)] == ["field-type"]  # its marker stays


def _outside(info, old: str, new: str) -> None:
    """A script's edit: the file changes, the copy in memory does not.

    Args:
        info: The `info.yaml` to edit.
        old: The text to replace.
        new: Its replacement.
    """
    info.write_text(info.read_text().replace(old, new))
    stat = info.stat()  # a distinct mtime, whatever the clock granularity
    os.utime(info, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_a_stale_document_is_checked_as_its_file_holds_it_not_as_loaded(tmp_path, papis_lib):
    """A stale document is checked as its file holds it not as loaded.

    No reload after the outside edits, so the copy in memory keeps `bad ref!`:
    findings from it stored under the new mtime would never go stale again.

    Args:
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = broken_doc(tmp_path)
    assert [finding.name for finding in doctor.scan(doc, ["refs"])] == ["refs"]

    _outside(info, "ref: bad ref!", "ref: fine")
    doctor.peek(doc)
    doctor.recheck()  # the background path: a fresh read, the shared copy untouched
    assert doctor.cached(doc) == []
    assert doc["ref"] == "bad ref!"

    _outside(info, "ref: fine", "ref: fine-too")
    assert doctor.scan(doc, ["refs"]) == []  # the main-thread path re-reads in place
    assert doc["ref"] == "fine-too"


def test_findings_are_read_only(tmp_path):
    """Findings are read only.

    The whole point: looking must not change the document. papis's own
    `doctor.run` defaults to `fix=True`, so this is the regression that matters.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    doc = papis.document.from_data({"title": "x", "type": "article", "ref": "bad ref!"})
    before = dict(doc)
    found = doctor.findings(doc)
    assert [finding.name for finding in found if finding.name == "refs"] == ["refs"]
    assert dict(doc) == before


def broken_doc(tmp_path):
    """Build a document *inside* the fixture library, with findings to fix.

    `safewrite.edit` resyncs the papis cache, and papis refuses to update a document it has never seen.

    Args:
        tmp_path: pytest's per-test temporary folder.

    Returns:
        `(document, its info.yaml)`.
    """
    folder = tmp_path / "lib" / "broken"
    folder.mkdir()
    (folder / "info.yaml").write_text(
        "# a comment that must survive\n"
        "title: A Title\n"
        "type: article\n"
        "ref: bad ref!\n"
        "year: '2024'\n"
        "papis_id: id-broken\n"
        "custom_key: kept\n"
    )
    import papis.database

    papis.database.clear_cached()
    doc = papis.document.from_folder(str(folder))
    return doc, folder / "info.yaml"


def test_fix_persists_only_what_the_check_changed(tmp_path, papis_lib):
    """Fix persists only what the check changed.

    Args:
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = broken_doc(tmp_path)
    refs = next(finding for finding in doctor.findings(doc, ["refs"]) if finding.fix_action)
    changed = doctor.fix(doc, refs)

    assert changed == {"ref": ("bad ref!", "badref!")}  # what it replaced, what it wrote
    text = info.read_text()
    assert "ref: badref!" in text
    assert "# a comment that must survive" in text  # round-trip invariant
    assert "custom_key: kept" in text  # unknown keys survive
    assert "year: '2024'" in text  # the refs fix must not touch anything else


def test_fix_without_a_fix_action_writes_nothing(tmp_path, papis_lib):
    """Fix without a fix action writes nothing.

    Args:
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = broken_doc(tmp_path)
    before = info.read_text()
    unfixable = doctor.Finding("made-up", str(info), "ref", "no fix here", None, doc)
    assert doctor.fix(doc, unfixable) == {}
    assert info.read_text() == before


def test_a_stale_file_aborts_the_fix_and_reloads(tmp_path, papis_lib, monkeypatch):
    """A stale file aborts the fix and reloads.

    Args:
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        monkeypatch: pytest's monkeypatch fixture.
    """
    doc, info = broken_doc(tmp_path)
    refs = next(finding for finding in doctor.findings(doc, ["refs"]) if finding.fix_action)

    def stale(_info):
        """Refuse the write, as a file changed on disk does.

        Args:
            _info: The `InfoFile`; unused.

        Raises:
            StaleError: Always.
        """
        raise safewrite.StaleError("changed under us")

    monkeypatch.setattr(safewrite, "write", stale)
    with pytest.raises(safewrite.StaleError):
        doctor.fix(doc, refs)
    # the in-memory fix must not survive a write that never happened
    assert doc["ref"] == "bad ref!"
    assert "ref: bad ref!" in info.read_text()


def only_refs(app):
    """Keep these tests about the view, not about papis's opinions.

    The fixture documents are minimal enough to trip `keys-missing` and `bibtex-type`, which would leave
    every row in the doctor narrow and prove nothing.

    Args:
        app: The app, whose `doctor.checks` is narrowed in place.
    """
    app.config.data.setdefault("doctor", {})["checks"] = ["refs"]


async def test_doctor_run_narrows_to_the_documents_with_findings(app, tmp_path):
    """The findings view is the list itself: `! !` narrows, `escape` drops it.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        tmp_path: pytest's per-test temporary folder.
    """
    from conftest import press

    _doc, info = broken_doc(tmp_path)
    before = info.read_text()
    async with app.run_test() as pilot:
        from ptui import actions, library

        only_refs(app)
        actions.reload(app)
        await press(pilot, "!", "!")
        assert info.read_text() == before  # reporting changed nothing
        assert app.query_one("#log-pane").display
        assert app.doctor_only
        assert [library.doc_id(doc) for doc in app.rows] == ["id-broken"]
        assert len(app.docs) == 4  # the three clean fixture documents are still scoped

        await press(pilot, "escape")
        assert not app.doctor_only
        assert len(app.rows) == 4
        assert info.read_text() == before


async def test_the_info_pane_shows_the_findings_of_the_current_document(app, tmp_path):
    """The info pane shows the findings of the current document.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        tmp_path: pytest's per-test temporary folder.
    """
    from conftest import press

    broken_doc(tmp_path)
    async with app.run_test() as pilot:
        from ptui import actions

        only_refs(app)
        actions.reload(app)
        await press(pilot, "!", "!")
        assert app.current["papis_id"] == "id-broken"
        pane = str(app.query_one("#info").render())
        assert "doctor" in pane
        assert "refs" in pane

        # a clean document says nothing about doctor (`g g`: the broken fixture has
        # no `time-added`, so it sorts *last* and `G` would land back on it)
        await press(pilot, "escape", "g", "g")
        assert "doctor" not in str(app.query_one("#info").render())


async def test_fix_pick_applies_the_selected_fix(app, tmp_path):
    """Fix pick applies the selected fix.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        tmp_path: pytest's per-test temporary folder.
    """
    from conftest import press

    _doc, info = broken_doc(tmp_path)
    async with app.run_test() as pilot:
        from ptui import actions, ui

        only_refs(app)
        actions.reload(app)
        await press(pilot, "!", "!")  # narrows onto the broken document
        await press(pilot, "!", "o")
        assert isinstance(app.screen, ui.SelectList)
        # pick the `refs` finding, whichever row it is on
        index = next(position for position, it in enumerate(app.screen.items) if it.hint == "refs")
        app.screen.query_one("OptionList").highlighted = index
        await press(pilot, "enter")
        assert "ref: badref!" in info.read_text()
        assert "# a comment that must survive" in info.read_text()
        # and the pane no longer claims a finding that has been fixed
        assert "refs" not in str(app.query_one("#info").render())

        await press(pilot, "u")  # one finding, one step
        assert "ref: bad ref!" in info.read_text()


async def test_undoing_a_fix_puts_the_field_back_and_redo_fixes_it_again(app, tmp_path):
    """Undoing a fix puts the field back and redo fixes it again.

    `! f` pushed no undo step, so `u` right after it undid the step before
    and the fixed field stayed fixed.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        tmp_path: pytest's per-test temporary folder.
    """
    from conftest import press

    from ptui import actions

    _doc, info = broken_doc(tmp_path)
    before = info.read_text()
    async with app.run_test() as pilot:
        only_refs(app)
        actions.reload(app)
        await press(pilot, "!", "!")  # narrows onto the broken document
        await press(pilot, "!", "f")  # one document: no question
        assert "ref: badref!" in info.read_text()

        await press(pilot, "u")
        assert info.read_text() == before  # the comment and the quoted year too

        await press(pilot, "ctrl+r")
        assert "ref: badref!" in info.read_text()


async def test_a_fix_that_renames_a_key_undoes_to_the_old_key_alone(app, papis_lib):
    """A fix that renames a key undoes to the old key alone.

    `biblatex-key-alias` writes `institution` and drops `school`: undo has to
    remove the key it added as well as restore the one it took away.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    from ptui import actions

    path = papis_lib / "lib" / "doc0" / "info.yaml"
    path.write_text(path.read_text() + "school: S\n")
    before = path.read_text()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        actions.cmdline_run(app, "doctor.fix", "biblatex-key-alias true")
        assert "institution: S" in path.read_text() and "school" not in path.read_text()
        actions.app_undo(app)
        assert path.read_text() == before


async def test_fixing_a_batch_asks_first_counting_the_marks_the_narrow_hides(app, papis_lib):
    """Fixing a batch asks first counting the marks the narrow hides.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    from conftest import press, settle

    from ptui import actions, ui

    paths = [papis_lib / "lib" / folder / "info.yaml" for folder in ("doc0", "doc1")]
    for path in paths:
        path.write_text(re.sub(r"ref: \w+", "ref: bad ref!", path.read_text()))
    before = [path.read_text() for path in paths]
    async with app.run_test() as pilot:
        await settle(pilot)
        only_refs(app)
        app.marks = {"id0", "id1"}
        app.apply_narrow("attention")
        await settle(pilot)
        assert len(app.rows) == 1

        await press(pilot, "!", "a")
        assert isinstance(app.screen, ui.SelectList)
        assert "2 documents" in app.screen.title_text
        await press(pilot, "escape")  # cancelled: nothing written, nothing recorded
        assert [path.read_text() for path in paths] == before
        assert not app.history.done

        await press(pilot, "!", "a", "enter")
        assert all("ref: badref!" in path.read_text() for path in paths)

        actions.app_undo(app)  # one step for both
        assert [path.read_text() for path in paths] == before
        actions.app_redo(app)
        assert all("ref: badref!" in path.read_text() for path in paths)


async def test_a_fix_that_changes_nothing_is_not_counted_as_fixed(app):
    """A fix that changes nothing is not counted as fixed.

    papis's `keys-missing` fix cannot invent an author, so it leaves the
    document as it was — and `! f` used to report it fixed.

    Args:
        app: The `PtuiApp` fixture, not started yet.
    """
    from textual.widgets import RichLog

    from ptui import actions

    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        doc = app.current
        noop = doctor.Finding(
            name="keys-missing", path="", payload="author", msg="no author", fix_action=lambda: None, doc=doc
        )
        assert actions._apply_one(app, doc, noop) == {}
        app.query_one(RichLog).display = True
        await pilot.pause(0.2)
        assert "changed nothing" in app.query_one(RichLog).lines[-1].text


async def test_the_startup_scan_marks_the_rows_it_found_something_on(papis_lib):
    """The startup scan marks the rows it found something on.

    The scan used to repaint only the info pane, so the list's markers showed
    up row by row as other redraws happened to reach them.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    from conftest import build_app, settle

    from ptui import ui

    app = build_app(papis_lib)
    app.config.data["doctor"]["scan_on_startup"] = True
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await settle(pilot)
        title = next(position for position, (column, _) in enumerate(app._fit) if not column["width"])
        cells = [str(app.query_one("DataTable").get_row_at(row)[title]) for row in range(3)]
        assert all(cell.startswith(ui.glyph("warning")) for cell in cells)  # bare fixtures trip bibtex-type


async def test_an_unchecked_document_does_not_claim_zero_findings(app):
    """An unchecked document does not claim zero findings.

    Args:
        app: The `PtuiApp` fixture, not started yet.
    """
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        pane = str(app.query_one("#info").render())
        assert "not checked" in pane
        assert "(0)" not in pane


async def test_doctor_fix_with_a_check_name_fixes_that_check_only(app, tmp_path):
    """Doctor fix with a check name fixes that check only.

    `:doctor.fix refs` — one check across the shown set; `field-type` would
    also have turned the quoted year into a number.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        tmp_path: pytest's per-test temporary folder.
    """
    from ptui import actions

    _doc, info = broken_doc(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        actions.reload(app)
        actions.cmdline_run(app, "doctor.fix", "refs")
        text = info.read_text()
        assert "ref: badref!" in text
        assert "year: '2024'" in text
