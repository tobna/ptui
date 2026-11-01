"""Marks: toggling at the edges, inverting and clearing around a narrow, the
marked-only view, marks across a scope change, and visual mode feeding a verb."""

import pytest
from conftest import build_app, press, settle
from cover_helpers import doc, logs

from ptui import actions, library, ui


def _row(app) -> str:
    return library.doc_id(app.current)


async def test_space_marks_and_moves_on_and_on_the_last_row_toggles_in_place(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space")
        assert app.marks == {"id0"}
        assert _row(app) == "id1"  # marks.advance
        await press(pilot, "G", "space")
        assert app.marks == {"id0", "id2"}
        assert _row(app) == "id2"  # nowhere to advance to
        await press(pilot, "space")
        assert app.marks == {"id0"}  # the same row again: unmarked


async def test_without_advance_the_cursor_stays_on_the_marked_row(papis_lib):
    app = build_app(papis_lib, "[marks]\nadvance = false\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space")
        assert (app.marks, _row(app)) == ({"id0"}, "id0")


async def test_inverting_inside_a_narrow_leaves_the_hidden_marks_alone(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id2"}
        await press(pilot, "/", *"year:>2010", "enter")  # id0 and id1 shown
        await settle(pilot)
        await press(pilot, "m", "i")
        assert app.marks == {"id1", "id2"}  # id0 flipped off, id1 on, id2 untouched
        await press(pilot, "m", "i")
        assert app.marks == {"id0", "id2"}  # its own inverse


async def test_clearing_marks_clears_the_hidden_ones_too(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id2"}
        await press(pilot, "/", *"attention", "enter")
        await settle(pilot)
        await press(pilot, "m", "c")
        assert app.marks == set()


async def test_marked_only_shows_the_marks_and_toggles_back(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "m", "o")
        assert app.rows == []  # nothing marked yet: nothing to show
        await press(pilot, "m", "o")
        app.marks = {"id1"}
        await press(pilot, "m", "o")
        assert [library.doc_id(d) for d in app.rows] == ["id1"]
        await press(pilot, "m", "o")
        assert len(app.rows) == 3


async def test_marks_survive_a_scope_that_hides_them_and_come_back_with_it(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space")  # id0
        actions.scope(app, "year:2016")
        await settle(pilot)
        assert app.marks == {"id0"}
        actions.scope(app, "")
        await settle(pilot)
        assert app.marks == {"id0"}
        assert library.doc_id(app.targets[0]) == "id0"


async def test_a_batch_acts_on_every_mark_not_on_the_cursor(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id1", "id2"}
        await press(pilot, "g", "g")  # the cursor on id0, which is not marked
        actions.doc_tag(app, "batch")
        await press(pilot, "enter")
        assert "batch" not in doc(app, "id0")["tags"]
        assert all("batch" in doc(app, i)["tags"] for i in ("id1", "id2"))


async def test_mark_query_that_matches_nothing_marks_nothing_and_says_so(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.mark_query(app, "year:<1900")
        assert app.marks == set()
        assert any("marked 0" in line for line in logged)


async def test_an_empty_mark_prompt_marks_nothing(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "m", "q", "enter")
        assert app.marks == set()


@pytest.mark.parametrize("keys", [("space",), ("m", "a"), ("m", "i"), ("m", "c"), ("m", "q", "x", "enter")])
async def test_every_other_mark_command_ends_a_visual_range(app, keys):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "v")
        assert app.visual is not None
        await press(pilot, *keys)
        assert app.visual is None


async def test_a_verb_pressed_mid_range_acts_on_what_is_drawn(app):
    """SPEC: visual marks live, so `c t` in the middle of a range tags it."""
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "v", "j")
        assert app.marks == {"id0", "id1"}
        await press(pilot, "c", "t", *"range", "enter")
        assert isinstance(app.screen, ui.SelectList)  # two targets: a batch confirms
        await press(pilot, "enter")
        await settle(pilot)
        assert [("range" in doc(app, i)["tags"]) for i in ("id0", "id1", "id2")] == [True, True, False]


async def test_a_narrow_ends_the_range_and_keeps_what_it_marked(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "v", "j")
        await press(pilot, "/", *"deep", "enter")
        await settle(pilot)
        assert app.visual is None
        assert app.marks == {"id0", "id1"}
        await press(pilot, "j")  # moving now marks nothing new
        assert app.marks == {"id0", "id1"}


async def test_a_visual_range_started_on_marks_keeps_them_when_it_shrinks(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id2"}
        await press(pilot, "v", "j", "j")
        assert app.marks == {"id0", "id1", "id2"}
        await press(pilot, "k", "k")  # back to the anchor
        assert app.marks == {"id0", "id2"}  # id2 was marked before the range
