"""Moving around: the list cursor and its bounds, the files pane's own cursor,
an empty scope, the pane commands, and the escape chain."""

import pytest
from conftest import press, settle
from cover_helpers import attach, logs

from ptui import actions, commands, library


def _row(app) -> str | None:
    return library.doc_id(app.current) if app.current else None


@pytest.mark.parametrize(
    ("keys", "at"),
    [
        (("j",), "id1"),
        (("j", "j", "j", "j"), "id2"),  # stops on the last row
        (("k",), "id0"),  # stops on the first
        (("G",), "id2"),
        (("G", "g", "g"), "id0"),
        (("ctrl+d",), "id2"),  # a page is more than three rows
        (("G", "ctrl+u"), "id0"),
    ],
)
async def test_the_cursor_moves_and_stops_at_either_end(app, keys, at):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, *keys)
        assert _row(app) == at


async def test_a_count_from_the_command_line_moves_that_many_rows(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.nav_down(app, 2)
        assert _row(app) == "id2"
        actions.nav_up(app, 5)
        assert _row(app) == "id0"


async def test_moving_shows_the_new_document_in_the_info_pane(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "j")
        assert app._info_of == "id1"


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("nav.down", {}),
        ("nav.up", {}),
        ("nav.top", {}),
        ("nav.bottom", {}),
        ("nav.page_down", {}),
        ("nav.page_up", {}),
        ("mark.toggle", {}),
        ("mark.all_filtered", {}),
        ("mark.invert", {}),
        ("visual.start", {}),
        ("doc.open", {}),
        ("files.open_pick", {}),
        ("doc.open_folder", {}),
        ("doc.browse", {}),
        ("doc.edit_raw", {}),
        ("doc.notes", {}),
        ("doc.tag", {"tags": "x"}),
        ("doc.untag", {"tags": "test"}),
        ("doc.set", {"key": "note", "value": "x"}),
        ("doc.status", {"value": "read"}),
        ("doc.rating", {"value": 3}),
        ("files.detach", {}),
        ("files.reorder", {}),
        ("files.rename", {}),
        ("files.repoint", {}),
        ("files.attach", {"path": "/nonexistent.pdf"}),
        ("export.citekey", {}),
        ("export.path", {}),
        ("export.url", {}),
        ("export.bibtex", {}),
        ("doctor.run", {}),
        ("doc.move", {"to": "test"}),
    ],
)
async def test_every_verb_on_an_empty_scope_does_nothing_and_does_not_crash(app, papis_lib, name, args):
    """A scope that matches nothing leaves no current document and no targets:
    every verb must find that out before it touches anything."""
    before = {p: p.read_text() for p in (papis_lib / "lib").rglob("info.yaml")}
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.scope(app, "title:nothing-has-this-title")
        assert app.rows == [] and app.current is None
        commands.run(name, app, args)  # directly: run_command would swallow a crash
        await settle(pilot)
        assert app.marks == set()
        assert not app.history.done
    assert {p: p.read_text() for p in (papis_lib / "lib").rglob("info.yaml")} == before


async def test_an_empty_scope_says_no_document_in_the_info_pane(app):
    from textual.widgets import Static

    async with app.run_test() as pilot:
        await settle(pilot)
        actions.scope(app, "title:nothing-has-this-title")
        assert "no document" in str(app.query_one("#info", Static).render())


async def test_in_the_files_pane_j_and_k_move_the_file_cursor_not_the_list(app, papis_lib):
    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3")
        await press(pilot, "j", "j", "j")
        assert app.file_index == 1  # two files: it stops on the last
        assert _row(app) == "id0"
        await press(pilot, "k", "k", "k")
        assert app.file_index == 0
        assert _row(app) == "id0"


async def test_the_list_pane_cannot_be_hidden(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.pane_toggle(app, "list")
        assert app.query_one("#list-pane").display
        assert any("cannot be hidden" in line for line in logged)


async def test_hiding_the_focused_info_pane_hands_the_keyboard_back_to_the_list(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        info = app.query_one("#info-pane")
        await press(pilot, "2")
        assert app.mode == "info"
        actions.pane_toggle(app, "info")  # `z i` is the list's key; `:` reaches it from here
        assert not info.display
        assert app.mode == "list"  # never focused on something invisible
        actions.pane_toggle(app, "info")
        assert info.display
        assert app.mode == "list"  # showing it does not steal focus


async def test_toggling_the_files_pane_focuses_it_and_toggling_again_leaves(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "z", "f")
        assert app.mode == "files"
        assert app.query_one("#files-pane").display
        await press(pilot, "z", "f")  # the files pane binds it too
        assert app.mode == "list"
        assert not app.query_one("#files-pane").display


@pytest.mark.parametrize("command", [actions.pane_toggle, actions.pane_focus])
async def test_an_unknown_pane_is_reported_and_changes_nothing(app, monkeypatch, command):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        command(app, "preview")
        assert app.mode == "list"
        assert any("no preview pane" in line for line in logged)


async def test_the_split_stays_on_screen_however_far_it_is_pushed(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        for _ in range(20):
            actions.pane_resize(app, 0.05)
        assert app.split == pytest.approx(0.9)
        for _ in range(40):
            actions.pane_resize(app, -0.05)
        assert app.split == pytest.approx(0.1)


async def test_an_explicit_layout_survives_a_resize(app):
    async with app.run_test(size=(200, 30)) as pilot:
        await settle(pilot)
        assert app.side_by_side  # wide: auto puts them side by side
        await press(pilot, "z", "z")
        assert not app.side_by_side
        assert not app.layout_auto
        await pilot.resize_terminal(220, 30)
        await settle(pilot)
        assert not app.side_by_side  # auto would have flipped it back


@pytest.mark.parametrize(("back", "order"), [(False, ["info", "files", "log", "list"]), (True, ["log", "files"])])
async def test_cycling_walks_the_panes_and_wraps(app, back, order):
    async with app.run_test() as pilot:
        await settle(pilot)
        seen = []
        for _ in order:
            actions.pane_cycle(app, back)
            seen.append(app.mode)
        assert seen == order


async def test_escape_ends_a_range_first_then_drops_the_narrow_and_never_the_marks(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "/", *"e", "enter")  # every title has an e
        await settle(pilot)
        await press(pilot, "v", "j")
        assert app.visual is not None
        marked = set(app.marks)
        assert len(marked) == 2

        await press(pilot, "escape")
        assert app.visual is None
        assert app.narrow_query == "e"  # one step at a time

        await press(pilot, "escape")
        assert app.narrow_query == ""
        await press(pilot, "escape")  # nothing left to drop
        assert app.marks == marked


async def test_escape_drops_the_doctor_view_like_a_narrow(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.doctor_only = True
        app.refilter()
        await press(pilot, "escape")
        assert not app.doctor_only
        assert len(app.rows) == 3


async def test_escape_with_a_prompt_open_closes_the_prompt_and_keeps_the_narrow(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.query_narrow(app, "neural")
        await settle(pilot)
        app.open_prompt("tag", "tags to add")
        actions.app_escape(app)  # the modal step: what `escape` reaches with a prompt up
        assert app.prompt_kind == ""
        assert app.narrow_query == "neural"


async def test_escape_with_nothing_to_drop_clears_a_pending_chord(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.pending = ("g",)
        actions.app_escape(app)
        assert app.pending == ()
