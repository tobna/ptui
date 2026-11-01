import os

import papis.document
import pytest

from ptui import doctor, safewrite


def test_check_names_defaults_to_every_registered_check():
    assert doctor.check_names([]) == doctor.check_names(None)
    assert "refs" in doctor.check_names([])
    assert doctor.check_names(["refs", "nope"]) == ["refs"]  # unknown names dropped
    assert doctor.unknown_checks(["refs", "nope"]) == ["nope"]
    assert doctor.check_names(["key-type"]) == ["field-type"]  # papis's rename still works
    assert doctor.unknown_checks(["key-type"]) == []


def test_library_wide_checks_are_kept_out_of_the_per_document_set():
    per_doc, wide = doctor.check_names([]), doctor.check_names([], library_wide=True)
    assert wide == ["duplicated-keys"]
    assert set(per_doc) & set(wide) == set()
    assert "duplicated-values" in per_doc  # per-document despite the name


def test_looking_twice_at_a_document_finds_the_same_thing():
    """The B2.1 regression: `duplicated-keys` accumulates seen values in papis's
    module state, so it used to invent findings on the second look."""
    doc = papis.document.from_data({"title": "x", "type": "article", "ref": "ok", "doi": "10.1/a"})
    runs = [len(doctor.findings(doc)) for _ in range(4)]
    assert len(set(runs)) == 1


def test_library_wide_scan_is_repeatable_and_reports_the_second_document():
    docs = [  # `doctor-duplicated-keys-keys` defaults to ["ref"]
        papis.document.from_data({"title": "a", "ref": "same"}),
        papis.document.from_data({"title": "b", "ref": "same"}),
    ]
    found = doctor.scan_library(docs)
    assert [f.name for _doc, f in found] == ["duplicated-keys"]
    assert found[0][0]["title"] == "b"  # the duplicate is the second one to claim it
    assert len(doctor.scan_library(docs)) == 1  # state reset, so a re-run is identical


def test_a_written_document_is_rechecked_on_the_next_look(tmp_path, papis_lib):
    """Not "not checked": one document is ~2 ms, so a stale entry is re-scanned
    with the checks it was scanned with, and an edit shows its effect at once."""
    doc, _info = broken_doc(tmp_path)
    assert doctor.cached(doc) is None  # never scanned: unknown, not clean
    found = doctor.scan(doc, ["refs"])
    assert doctor.cached(doc) == found
    assert [f.name for f in found] == ["refs"]

    doctor.fix(doc, found[0])
    assert doctor.cached(doc) == []  # re-checked against the written file
    assert doctor.CACHE[doc["papis_id"]][2] == ["refs"]  # with the same checks


def test_peek_keeps_the_old_findings_and_queues_the_document(tmp_path, papis_lib):
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
    """A `git pull` that touches every document must not freeze the next redraw."""
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
    doc, info = broken_doc(tmp_path)  # a bad ref, and a year stored as text
    assert sorted(f.name for f in doctor.scan(doc, ["refs", "field-type"])) == ["field-type", "refs"]
    assert [f.name for f in doctor.scan(doc, ["refs"])] == ["refs"]  # returns what it ran
    assert [f.name for f in doctor.cached(doc)] == ["field-type", "refs"]  # keeps the rest
    assert doctor.CACHE[doc["papis_id"]][2] == ["field-type", "refs"]

    # written since: the other check runs again too, or the new stamp would vouch for it
    info.write_text(info.read_text().replace("year: '2024'", "year: 2024"))
    stat = info.stat()
    os.utime(info, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    doc.load()
    doctor.scan(doc, ["refs"])
    assert [f.name for f in doctor.cached(doc)] == ["refs"]


async def test_a_named_doctor_run_narrows_by_its_check_and_keeps_the_other_markers(app, tmp_path, papis_lib):
    from ptui import actions, library

    broken_doc(tmp_path)  # refs and field-type
    info = papis_lib / "lib" / "doc1" / "info.yaml"
    info.write_text(info.read_text().replace("year: 2016", "year: '2016'"))  # field-type only
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app.cfg.data.setdefault("doctor", {})["checks"] = ["refs", "field-type"]
        actions.reload(app)
        actions.doctor_run(app)
        assert sorted(library.doc_id(d) for d in app.rows) == ["id-broken", "id1"]

        actions.cmdline_run(app, "doctor.run", "refs")
        assert [library.doc_id(d) for d in app.rows] == ["id-broken"]  # what refs found
        doc1 = next(d for d in app.docs if library.doc_id(d) == "id1")
        assert [f.name for f in doctor.cached(doc1)] == ["field-type"]  # its marker stays


def _outside(info, old: str, new: str) -> None:
    """A script's edit: the file changes, the copy in memory does not."""
    info.write_text(info.read_text().replace(old, new))
    stat = info.stat()  # a distinct mtime, whatever the clock granularity
    os.utime(info, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_a_stale_document_is_checked_as_its_file_holds_it_not_as_loaded(tmp_path, papis_lib):
    """No reload after the outside edits, so the copy in memory keeps `bad ref!`:
    findings from it stored under the new mtime would never go stale again."""
    doc, info = broken_doc(tmp_path)
    assert [f.name for f in doctor.scan(doc, ["refs"])] == ["refs"]

    _outside(info, "ref: bad ref!", "ref: fine")
    doctor.peek(doc)
    doctor.recheck()  # the background path: a fresh read, the shared copy untouched
    assert doctor.cached(doc) == []
    assert doc["ref"] == "bad ref!"

    _outside(info, "ref: fine", "ref: fine-too")
    assert doctor.scan(doc, ["refs"]) == []  # the main-thread path re-reads in place
    assert doc["ref"] == "fine-too"


def test_findings_are_read_only(tmp_path):
    """The whole point: looking must not change the document. papis's own
    `doctor.run` defaults to `fix=True`, so this is the regression that matters."""
    doc = papis.document.from_data({"title": "x", "type": "article", "ref": "bad ref!"})
    before = dict(doc)
    found = doctor.findings(doc)
    assert [f.name for f in found if f.name == "refs"] == ["refs"]
    assert dict(doc) == before


def broken_doc(tmp_path):
    """A document *inside* the fixture library: `safewrite.edit` resyncs the papis
    cache, and papis refuses to update a document it has never seen."""
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
    doc, info = broken_doc(tmp_path)
    refs = next(f for f in doctor.findings(doc, ["refs"]) if f.fix_action)
    changed = doctor.fix(doc, refs)

    assert changed == ["ref"]
    text = info.read_text()
    assert "ref: badref!" in text
    assert "# a comment that must survive" in text  # round-trip invariant
    assert "custom_key: kept" in text  # unknown keys survive
    assert "year: '2024'" in text  # the refs fix must not touch anything else


def test_fix_without_a_fix_action_writes_nothing(tmp_path, papis_lib):
    doc, info = broken_doc(tmp_path)
    before = info.read_text()
    unfixable = doctor.Finding("made-up", str(info), "ref", "no fix here", None, doc)
    assert doctor.fix(doc, unfixable) == []
    assert info.read_text() == before


def test_a_stale_file_aborts_the_fix_and_reloads(tmp_path, papis_lib, monkeypatch):
    doc, info = broken_doc(tmp_path)
    refs = next(f for f in doctor.findings(doc, ["refs"]) if f.fix_action)

    def stale(_info):
        raise safewrite.StaleError("changed under us")

    monkeypatch.setattr(safewrite, "write", stale)
    with pytest.raises(safewrite.StaleError):
        doctor.fix(doc, refs)
    # the in-memory fix must not survive a write that never happened
    assert doc["ref"] == "bad ref!"
    assert "ref: bad ref!" in info.read_text()


def only_refs(app):
    """Keep these tests about the view, not about papis's opinions: the fixture
    documents are minimal enough to trip `keys-missing` and `bibtex-type`, which
    would leave every row in the doctor narrow and prove nothing."""
    app.cfg.data.setdefault("doctor", {})["checks"] = ["refs"]


async def test_doctor_run_narrows_to_the_documents_with_findings(app, tmp_path):
    """The findings view is the list itself: `! !` narrows, `escape` drops it."""
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
        assert [library.doc_id(d) for d in app.rows] == ["id-broken"]
        assert len(app.docs) == 4  # the three clean fixture documents are still scoped

        await press(pilot, "escape")
        assert not app.doctor_only
        assert len(app.rows) == 4
        assert info.read_text() == before


async def test_the_info_pane_shows_the_findings_of_the_current_document(app, tmp_path):
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
        index = next(i for i, it in enumerate(app.screen.items) if it.hint == "refs")
        app.screen.query_one("OptionList").highlighted = index
        await press(pilot, "enter")
        assert "ref: badref!" in info.read_text()
        assert "# a comment that must survive" in info.read_text()
        # and the pane no longer claims a finding that has been fixed
        assert "refs" not in str(app.query_one("#info").render())


async def test_a_fix_that_changes_nothing_is_not_counted_as_fixed(app):
    """papis's `keys-missing` fix cannot invent an author, so it leaves the
    document as it was — and `! f` used to report it fixed."""
    from textual.widgets import RichLog

    from ptui import actions

    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        doc = app.current
        noop = doctor.Finding(
            name="keys-missing", path="", payload="author", msg="no author", fix_action=lambda: None, doc=doc
        )
        assert actions._apply_one(app, doc, noop) is False
        app.query_one(RichLog).display = True
        await pilot.pause(0.2)
        assert "changed nothing" in app.query_one(RichLog).lines[-1].text


async def test_the_startup_scan_marks_the_rows_it_found_something_on(papis_lib):
    """The scan used to repaint only the info pane, so the list's markers showed
    up row by row as other redraws happened to reach them."""
    from conftest import build_app, settle

    from ptui import ui

    app = build_app(papis_lib)
    app.cfg.data["doctor"]["scan_on_startup"] = True
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await settle(pilot)
        title = next(i for i, (column, _) in enumerate(app._fit) if not column["width"])
        cells = [str(app.query_one("DataTable").get_row_at(row)[title]) for row in range(3)]
        assert all(cell.startswith(ui.glyph("warning")) for cell in cells)  # bare fixtures trip bibtex-type


async def test_an_unchecked_document_does_not_claim_zero_findings(app):
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        pane = str(app.query_one("#info").render())
        assert "not checked" in pane
        assert "(0)" not in pane


async def test_doctor_fix_with_a_check_name_fixes_that_check_only(app, tmp_path):
    """`:doctor.fix refs` — one check across the shown set; `field-type` would
    also have turned the quoted year into a number."""
    from ptui import actions

    _doc, info = broken_doc(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        actions.reload(app)
        actions.cmdline_run(app, "doctor.fix", "refs")
        text = info.read_text()
        assert "ref: badref!" in text
        assert "year: '2024'" in text
