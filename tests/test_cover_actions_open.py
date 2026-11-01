"""Handing a document to the desktop and the editor, and yanking it: which file
opens, `opened_at`, `$EDITOR` round trips, notes, and every export verb on a
document that lacks what it exports. Every opener is `true` (conftest)."""

import contextlib
from pathlib import Path

import papis.api
import papis.commands.edit
import papis.utils
import pytest
import yaml
from conftest import build_app, press, settle
from cover_helpers import attach, info, logs

from ptui import actions, clip, ui


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    paths: list[str] = []
    monkeypatch.setattr(papis.utils, "open_file", lambda path, **_: paths.append(Path(path).name))
    return paths


@pytest.fixture
def yanked(monkeypatch) -> list[str]:
    texts: list[str] = []
    monkeypatch.setattr(clip, "copy", lambda app, text: texts.append(text) or "test")
    return texts


async def test_open_with_no_files_says_so(app, opened, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "o")
        assert logged == ["[yellow]no files attached[/]"]
    assert opened == []


@pytest.mark.parametrize(("which", "name"), [(None, "a.pdf"), (1, "b.pdf"), (7, "a.pdf")])
async def test_open_takes_the_main_file_or_the_one_asked_for(app, papis_lib, opened, which, name):
    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_open(app, which)
    assert opened == [name]


async def test_the_main_file_opens_before_a_notes_file_listed_first(app, papis_lib, opened):
    attach(papis_lib, "doc0", "talk_notes.pdf", "paper.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "o")
    assert opened == ["paper.pdf"]


async def test_opening_records_opened_at_only_when_asked_to(papis_lib, opened):
    attach(papis_lib, "doc0", "a.pdf")
    for track in (False, True):
        app = build_app(papis_lib)
        app.cfg.data["general"]["track_opens"] = track  # [general] is the fixture's already
        async with app.run_test() as pilot:
            await settle(pilot)
            await press(pilot, "o")
        assert ("opened_at" in yaml.safe_load(info(papis_lib).read_text())) is track
    assert opened == ["a.pdf", "a.pdf"]


async def test_the_open_picker_lists_every_file_and_flags_a_missing_one(app, papis_lib, opened):
    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    (papis_lib / "lib" / "doc0" / "b.pdf").unlink()
    attach(papis_lib, "doc0", "c.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "o")
        assert [(item.label, item.hint) for item in app.screen.items] == [
            ("a.pdf", ""),
            ("b.pdf", "missing"),
            ("c.pdf", ""),
        ]
        await press(pilot, *"c.pdf", "enter")
    assert opened == ["c.pdf"]


async def test_open_folder_hands_papis_the_document_folder(app, papis_lib, monkeypatch):
    folders = []
    monkeypatch.setattr(papis.api, "open_dir", folders.append)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "O")
    assert folders == [str(papis_lib / "lib" / "doc0")]


async def test_browse_logs_the_url_papis_built(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_set(app, "doi", "10.1000/xyz")
        logged = logs(app, monkeypatch)
        await press(pilot, "b")
        assert logged == ["browsing https://doi.org/10.1000/xyz"]


async def test_an_editor_session_that_changes_the_file_shows_the_change(app, papis_lib, monkeypatch):
    path = info(papis_lib)
    monkeypatch.setattr(
        papis.commands.edit, "run", lambda doc, **_: path.write_text(path.read_text().replace("2017", "2018"))
    )
    async with app.run_test() as pilot:
        await settle(pilot)
        monkeypatch.setattr(app, "suspend", contextlib.nullcontext)
        await press(pilot, "e")
        assert app.current["year"] == 2018
        assert "2018" in app.query_one("#info").render().plain


@pytest.mark.parametrize("key", ["enter", "escape"])
async def test_the_form_confirmed_or_cancelled_unchanged_writes_nothing(app, papis_lib, key):
    """Opening the editor and leaving it must not rewrite the file — not even a
    title with non-ASCII text, a list of tags or an int year read back as text."""
    path = info(papis_lib)
    path.write_text(path.read_text().replace("Attention Is All You Need", "Über Größe — 東京"))
    before = path.read_text()
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "E")
        assert isinstance(app.screen, ui.EditForm)
        await press(pilot, key)
        assert not app.history.done
    assert path.read_text() == before


async def test_notes_are_created_from_papis_and_recorded_on_the_document(app, papis_lib, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        monkeypatch.setattr(app, "suspend", contextlib.nullcontext)
        await press(pilot, "g", "n")  # editor = true: it returns at once
        notes = app.current["notes"]
    assert yaml.safe_load(info(papis_lib).read_text())["notes"] == notes
    assert (papis_lib / "lib" / "doc0" / notes).exists()


# ── export ──────────────────────────────────────────────────────────────────


async def test_a_citekey_for_a_document_with_no_ref_is_skipped_not_yanked_empty(app, papis_lib, yanked):
    path = info(papis_lib, "doc1")
    path.write_text(path.read_text().replace("ref: He2016\n", ""))
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        await press(pilot, "y", "y")
    assert yanked == ["\\cite{Vaswani2017}"]


async def test_nothing_to_yank_copies_nothing(app, yanked, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "y", "p", "y", "u")  # no files, no url, no doi
        assert logged == ["[yellow]nothing to yank for path[/]", "[yellow]nothing to yank for url[/]"]
    assert yanked == []


async def test_path_yanks_each_targets_main_file_absolute(app, papis_lib, yanked):
    attach(papis_lib, "doc0", "a_notes.pdf", "a.pdf")
    attach(papis_lib, "doc1", "b.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1", "id2"}  # id2 has no file: skipped
        await press(pilot, "y", "p")
    lib = papis_lib / "lib"
    assert yanked == [f"{lib / 'doc0' / 'a.pdf'}\n{lib / 'doc1' / 'b.pdf'}"]


async def test_url_prefers_the_url_then_a_doi_link(app, yanked):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_set(app, "doi", "10.1/a")
        actions.doc_set(app, "url", "https://example.org/a")
        app.marks = {"id0", "id1"}
        actions.doc_set(app, "doi", "10.1/b")
        await press(pilot, "enter")  # the batch confirm: both get 10.1/b
        await press(pilot, "y", "u")
    assert yanked == ["https://example.org/a\nhttps://doi.org/10.1/b"]


async def test_bibtex_of_a_batch_yanks_every_entry(app, yanked):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id2"}
        await press(pilot, "y", "b")
    assert yanked[0].count("@") == 2
    assert "Vaswani2017" in yanked[0] and "Bengio2003" in yanked[0]


async def test_help_lists_the_keys_of_the_pane_it_was_opened_from(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3", "?")
        assert isinstance(app.screen, ui.SelectList)
        labels = [item.label for item in app.screen.items]
        assert labels[0].startswith("escape")  # outside the list, escape is the way back
        assert any(item.value == "files.detach" for item in app.screen.items)
        assert not any(item.value == "doc.merge" for item in app.screen.items)  # a list key
