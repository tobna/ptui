import papis.document
import pytest

from ptui import doctor, safewrite


def test_check_names_defaults_to_every_registered_check():
    assert doctor.check_names([]) == doctor.check_names(None)
    assert "refs" in doctor.check_names([])
    assert doctor.check_names(["refs", "nope"]) == ["refs"]  # unknown names dropped
    assert doctor.unknown_checks(["refs", "nope"]) == ["nope"]


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


def test_the_cache_goes_stale_when_info_yaml_is_written(tmp_path, papis_lib):
    doc, _info = broken_doc(tmp_path)
    assert doctor.cached(doc) is None  # never scanned
    found = doctor.scan(doc)
    assert doctor.cached(doc) == found
    assert any(f.name == "refs" for f in found)

    refs = next(f for f in found if f.name == "refs" and f.fix_action)
    doctor.fix(doc, refs)
    assert doctor.cached(doc) is None  # the write invalidated it; unknown, not clean
    assert not any(f.name == "refs" for f in doctor.scan(doc))


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
    unfixable = doctor.Finding("made-up", str(info), "ref", "no fix here", "", None, doc)
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
