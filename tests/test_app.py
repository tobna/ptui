from conftest import press, settle


async def test_starts_and_lists_the_library(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        assert len(app.docs) == 3
        assert app.current["title"] == "Attention Is All You Need"  # sorted, newest first
        await press(pilot, "j")
        assert app.current["year"] == 2016


async def test_narrow_filters_without_touching_the_scope(app):
    async with app.run_test() as pilot:
        await press(pilot, "/")
        assert app.prompt_kind == "narrow"
        await press(pilot, "r", "e", "s", "i", "d")
        await settle(pilot)
        assert [d["year"] for d in app.rows] == [2016]
        assert len(app.docs) == 3
        await press(pilot, "escape")  # closes the prompt...
        await press(pilot, "escape")  # ...then clears the narrow
        await settle(pilot)
        assert len(app.rows) == 3


async def test_marks_survive_narrowing(app):
    async with app.run_test() as pilot:
        await press(pilot, "space")  # marks and advances
        assert app.marks == {"id0"}
        await press(pilot, "/")
        await press(pilot, "b", "e", "n", "g")
        await settle(pilot)
        assert len(app.rows) == 1
        assert app.marks == {"id0"}  # still marked though invisible
        await press(pilot, "escape", "escape")
        await settle(pilot)
        assert len(app.targets) == 1  # batch commands act on the mark, not the cursor


async def test_chord_and_unknown_command(app):
    async with app.run_test() as pilot:
        await press(pilot, "j")
        await press(pilot, "g", "g")
        assert app.current["year"] == 2017
        await press(pilot, "g")
        assert app.pending == ("g",)
        await press(pilot, "z")  # no such chord
        assert app.pending == ()


async def test_escape_leaves_any_mode(app):
    async with app.run_test() as pilot:
        await press(pilot, "4")  # the log mode defines no bindings at all
        assert app.mode == "log"
        await press(pilot, "escape")
        assert app.mode == "list"
        await press(pilot, "tab")  # info: defines four keys, none of them q
        assert app.mode == "info"
        await press(pilot, "g")  # a pending prefix must not survive the escape
        await press(pilot, "escape")
        assert (app.mode, app.pending) == ("list", ())


async def test_z_chords_resize_the_panes(app):
    async with app.run_test(size=(80, 24)) as pilot:
        table, info, panes = (
            app.query_one(f"#{name}") for name in ("list-pane", "info-pane", "panes")
        )
        await settle(pilot)
        assert table.size.width > info.size.width  # the list is the larger pane
        await press(pilot, "z", "z")  # stacked: the split moves to the other axis
        assert min(table.size.width, info.size.width) > panes.size.width - 5  # both full width
        assert table.size.height > info.size.height
        await press(pilot, "z", "i")  # the only pane left takes the whole window
        assert not info.display
        assert table.size.height > panes.size.height - 3  # minus its own border
        await press(pilot, "z", "z", "z", "i")
        assert table.size.width < panes.size.width  # info is back, side by side


async def test_sort_reverse_keeps_the_cursor_on_the_document(app):
    async with app.run_test() as pilot:
        await press(pilot, "S")  # unimplemented in v0: logs, does not crash
        await press(pilot, "j")
        current = app.current["title"]
        await press(pilot, "ctrl+s")
        await settle(pilot)
        assert app.current["title"] == current
