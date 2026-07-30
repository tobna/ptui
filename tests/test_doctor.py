import papis.document
import pytest

from ptui import doctor, safewrite


def test_check_names_defaults_to_every_registered_check():
    assert doctor.check_names([]) == doctor.check_names(None)
    assert "refs" in doctor.check_names([])
    assert doctor.check_names(["refs", "nope"]) == ["refs"]  # unknown names dropped
    assert doctor.unknown_checks(["refs", "nope"]) == ["nope"]


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


async def test_doctor_run_reports_and_never_writes(app, tmp_path):
    from conftest import press

    _doc, info = broken_doc(tmp_path)
    before = info.read_text()
    async with app.run_test() as pilot:
        from ptui import actions

        actions.reload(app)
        await press(pilot, "/", "a", " ", "t", "i", "t", "l", "e")
        await press(pilot, "escape")
        assert app.current["ref"] == "bad ref!"
        await press(pilot, "\\", "d")  # doctor.run
        assert info.read_text() == before  # reporting changed nothing
        assert app.query_one("#log-pane").display

        await press(pilot, "g", "d")  # view.doctor
        from ptui import ui

        assert isinstance(app.screen, ui.SelectList)
        labels = [item.label for item in app.screen.items]
        assert any("reference" in label.lower() for label in labels)
        assert info.read_text() == before  # browsing changed nothing either
        await press(pilot, "escape")
        assert info.read_text() == before


async def test_view_doctor_applies_the_selected_fix(app, tmp_path):
    from conftest import press

    _doc, info = broken_doc(tmp_path)
    async with app.run_test() as pilot:
        from ptui import actions, ui

        actions.reload(app)
        await press(pilot, "/", "a", " ", "t", "i", "t", "l", "e", "escape")
        await press(pilot, "g", "d")
        assert isinstance(app.screen, ui.SelectList)
        # pick the `refs` finding, whichever row it is on
        index = next(i for i, it in enumerate(app.screen.items) if it.hint == "refs")
        app.screen.query_one("OptionList").highlighted = index
        await press(pilot, "enter")
        assert "ref: badref!" in info.read_text()
        assert "# a comment that must survive" in info.read_text()
