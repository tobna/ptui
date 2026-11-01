"""The `c` verbs and their undo: what reaches `info.yaml`, what survives a write,
a batch where one write fails, a cancelled batch, and undo/redo around a re-sort."""

import os

import pytest
import yaml
from conftest import press, settle
from cover_helpers import doc, info, logs

from ptui import actions, library, safewrite, ui


def _disk(papis_lib, folder: str = "doc0") -> dict:
    return yaml.safe_load(info(papis_lib, folder).read_text())


async def test_a_write_keeps_comments_unknown_keys_and_their_order(app, papis_lib):
    """SPEC's round-trip invariant: a user's script adds keys ptui never heard of."""
    path = info(papis_lib)
    path.write_text("# kept by my script\n" + path.read_text() + "zz_custom: {nested: [1, 2]}  # mine\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_set(app, "year", "2020")
    text = path.read_text()
    assert text.startswith("# kept by my script\n")
    assert "zz_custom: {nested: [1, 2]}  # mine" in text
    assert list(_disk(papis_lib))[-1] == "zz_custom"
    assert _disk(papis_lib)["year"] == 2020


async def test_non_ascii_values_are_written_as_text_not_escapes(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "f", *"note", "space")
        app.query_one("#prompt").value += "Größe — 東京"  # what an IME would put there
        await press(pilot, "enter")
        assert app.current["note"] == "Größe — 東京"
    assert "note: Größe — 東京" in info(papis_lib).read_text()


async def test_an_undeclared_new_key_keeps_its_leading_zero(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_set(app, "volume", "04")
    assert _disk(papis_lib)["volume"] == "04"


async def test_set_from_the_command_line_takes_a_quoted_value(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.cmdline_run(app, "doc.set", 'note "two words"')
    assert _disk(papis_lib)["note"] == "two words"


@pytest.mark.parametrize("line", ["", "   "])
async def test_an_empty_set_prompt_writes_nothing(app, papis_lib, line):
    before = info(papis_lib).read_text()
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.prompt_result(app, "set", line)
        assert not app.history.done
    assert info(papis_lib).read_text() == before


@pytest.mark.parametrize(("command", "text"), [(actions.doc_tag, " , "), (actions.doc_untag, "")])
async def test_tagging_with_no_tag_writes_nothing(app, papis_lib, command, text):
    before = info(papis_lib).read_text()
    async with app.run_test() as pilot:
        await settle(pilot)
        command(app, text)
        assert not app.history.done
    assert info(papis_lib).read_text() == before


async def test_removing_a_key_the_document_lacks_is_harmless_and_undoes_to_nothing(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_set(app, "doi", "")
        assert "doi" not in app.current
        await press(pilot, "u")
        assert "doi" not in app.current
    assert "doi" not in _disk(papis_lib)


async def test_the_untag_prompt_names_the_tags_there_are(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "T")
        assert app.prompt_kind == "untag"
        assert "has: test" in app.query_one("#prompt").placeholder


async def test_the_status_picker_opens_on_the_current_status_and_takes_any_string(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_status(app, "reading")
        await press(pilot, "c", "s")
        assert app.screen.current == "reading"
        await press(pilot, "escape")
        actions.cmdline_run(app, "doc.status", "submitted")  # a free string still works
        assert app.current["reading_status"] == "submitted"


async def test_the_rating_picker_opens_on_the_current_rating(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_rating(app, 4)
        await press(pilot, "c", "r")
        assert isinstance(app.screen, ui.SelectList)
        assert app.screen.current == 4


async def test_cancelling_a_batch_status_writes_nothing_and_records_nothing(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        await press(pilot, "c", "s", *"read", "enter")  # the value...
        assert isinstance(app.screen, ui.SelectList)  # ...then the batch confirm
        await press(pilot, "escape")
        assert not app.history.done
    assert all("reading_status" not in _disk(papis_lib, f) for f in ("doc0", "doc1"))


async def test_an_edit_to_the_sort_key_moves_the_row_and_undo_moves_it_back(app):
    """SPEC: a `c` verb that changes `year` while sorted by year moves the row,
    the cursor following."""
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.sort_by(app, "year")  # desc: id0 (2017), id1, id2
        await press(pilot, "c", "f", *"year 1990", "enter")
        await settle(pilot)
        assert [library.doc_id(d) for d in app.rows] == ["id1", "id2", "id0"]
        assert library.doc_id(app.current) == "id0"  # the cursor went with it
        await press(pilot, "u")
        assert [library.doc_id(d) for d in app.rows] == ["id0", "id1", "id2"]
        assert library.doc_id(app.current) == "id0"
        await press(pilot, "ctrl+r")
        assert [library.doc_id(d) for d in app.rows][-1] == "id0"


async def test_undo_and_redo_with_nothing_to_do_say_so(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "u", "ctrl+r")
        assert logged == ["[yellow]nothing to undo[/]", "[yellow]nothing to redo[/]"]


async def test_a_new_edit_after_an_undo_drops_the_redo(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_set(app, "note", "one")
        await press(pilot, "u")
        actions.doc_set(app, "note", "two")
        await press(pilot, "ctrl+r")
        assert app.current["note"] == "two"  # "one" is not redone over it


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only folder")
async def test_a_batch_where_one_write_fails_writes_the_rest_and_undoes_them(app, papis_lib, monkeypatch):
    locked = papis_lib / "lib" / "doc1"
    locked.chmod(0o555)  # the temp file cannot be created: the write fails
    try:
        async with app.run_test() as pilot:
            await settle(pilot)
            logged = logs(app, monkeypatch)
            app.marks = {"id0", "id1", "id2"}
            actions.doc_set(app, "note", "batch")
            await press(pilot, "enter")
            assert [doc(app, i).get("note") for i in ("id0", "id1", "id2")] == ["batch", None, "batch"]
            assert any("note on He2016 failed" in line for line in logged)
            assert any("set note on 2/3" in line for line in logged)
            await press(pilot, "u")
            assert all("note" not in doc(app, i) for i in ("id0", "id1", "id2"))
    finally:
        locked.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only folder")
async def test_a_write_that_fails_everywhere_leaves_no_undo_step(app, papis_lib):
    locked = papis_lib / "lib" / "doc0"
    locked.chmod(0o555)
    try:
        async with app.run_test() as pilot:
            await settle(pilot)
            actions.doc_set(app, "note", "x")
            assert not app.history.done  # `u` must not offer an edit that never landed
    finally:
        locked.chmod(0o755)


async def test_a_stale_file_aborts_the_write_and_says_to_reload(app, papis_lib, monkeypatch):
    def stale(info):
        raise safewrite.StaleError("changed on disk")

    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        monkeypatch.setattr(safewrite, "write", stale)
        actions.doc_set(app, "note", "x")
        assert any("changed on disk; press r" in line for line in logged)
        assert not app.history.done
        assert "note" not in app.current


@pytest.mark.xfail(
    strict=True,
    reason="BUG: tags are computed from the in-memory document and written whole; a tag a "
    "script added to info.yaml since ptui loaded it is silently dropped (SPEC: safe write, "
    "never merge silently)",
)
async def test_tagging_after_a_script_tagged_the_file_keeps_the_scripts_tag(app, papis_lib):
    path = info(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        path.write_text(path.read_text().replace("tags: [test]", "tags: [test, from-script]"))
        actions.doc_tag(app, "mine")
    assert "from-script" in _disk(papis_lib)["tags"]


async def test_backfill_with_every_date_present_asks_nothing(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.lib_backfill_dates(app)
        assert not isinstance(app.screen, ui.SelectList)
        assert any("already has time-added" in line for line in logged)


async def test_a_cancelled_backfill_writes_nothing_and_a_confirmed_one_undoes_in_one_step(app, papis_lib):
    for folder in ("doc0", "doc1"):
        path = info(papis_lib, folder)
        path.write_text("".join(line for line in path.read_text().splitlines(True) if "time-added" not in line))
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.lib_backfill_dates(app)
        await press(pilot, "down", "enter")  # cancel
        assert not app.history.done
        actions.lib_backfill_dates(app)
        await press(pilot, "enter")
        assert all(doc(app, i).get(library.TIME_ADDED) for i in ("id0", "id1"))
        await press(pilot, "u")
        assert not any(doc(app, i).get(library.TIME_ADDED) for i in ("id0", "id1"))
        assert doc(app, "id2")[library.TIME_ADDED] == "2022-01-01-00:00:00"
