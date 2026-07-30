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
    # this is about the manual chords, so pin the layout: at 80 columns `auto`
    # would choose stacked on its own
    app.layout_auto = False
    app.side_by_side = True
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


async def test_auto_layout_stacks_until_the_flex_column_fits(app):
    assert app.layout_auto  # the shipped ui.layout
    async with app.run_test(size=(70, 24)) as pilot:
        await settle(pilot)
        assert not app.side_by_side  # too narrow: the title would be squeezed
        assert app.flex_width_if_side_by_side() < app.cfg.get("list.flex_target")

        await pilot.resize_terminal(200, 24)
        await settle(pilot)
        assert app.side_by_side  # room for both panes now

        await pilot.resize_terminal(70, 24)
        await settle(pilot)
        assert not app.side_by_side  # and back, on its own

        await press(pilot, "z", "z")  # an explicit choice ends automatic layout
        assert not app.layout_auto
        assert app.side_by_side
        await pilot.resize_terminal(60, 24)
        await settle(pilot)
        assert app.side_by_side  # a resize must not argue with the user


async def test_columns_fit_the_pane_and_drop_when_they_cannot(app):
    from textual.widgets import DataTable

    async with app.run_test(size=(160, 24)) as pilot:
        await settle(pilot)
        table = app.query_one(DataTable)
        widths = {column["title"]: width for column, width in app._fit}
        # the first column has no title: it is the type glyph, and the mark sits in it
        assert list(widths) == ["", "Year", "Author", "Title", "Tags"]
        assert widths["Author"] == 7  # p90 of Vaswani/He/Bengio, not the configured 18
        used = sum(width + table.cell_padding * 2 for _, width in app._fit)
        assert used <= table.size.width - 2  # never wider than the pane

        await pilot.resize_terminal(30, 24)
        await settle(pilot)
        assert "Author" not in [column["title"] for column, _ in app._fit]  # dropped
        assert app._fit[-1][1] >= 12  # the flex column keeps at least MIN_FLEX


async def test_an_optional_column_yields_to_the_flex_target(app):
    app.layout_auto = False  # forcing side by side is how the squeeze happens
    app.side_by_side = True
    target = app.cfg.get("list.flex_target")
    async with app.run_test(size=(110, 24)) as pilot:
        await settle(pilot)
        widths = {column["title"]: width for column, width in app._fit}
        assert "Tags" not in widths  # optional, and the title would have been starved
        assert widths["Title"] < target  # it did not even reach the target itself
        assert "Author" in widths  # a required column is never given up for it

        await pilot.resize_terminal(220, 24)
        await settle(pilot)
        widths = {column["title"]: width for column, width in app._fit}
        assert "Tags" in widths  # earned its width now
        assert widths["Title"] >= target


async def test_sort_direction_shows_in_the_header_of_the_sorted_column(app):
    from textual.widgets import DataTable

    from ptui import ui

    async with app.run_test(size=(160, 24)) as pilot:
        await settle(pilot)
        headers = lambda: [str(c.label) for c in app.query_one(DataTable).columns.values()]  # noqa: E731
        # the default sort is time-added, which no column shows
        assert not any(ui.glyph("sort_desc") in h for h in headers())

        await press(pilot, "S", "y", "e", "a", "r", "enter")  # sort by Year, descending
        await settle(pilot)
        assert f"Year {ui.glyph('sort_desc')}" in headers()

        await press(pilot, "S", "t", "i", "t", "l", "e", "enter")  # Title, ascending
        await settle(pilot)
        assert f"Title {ui.glyph('sort_asc')}" in headers()
        assert not any(h.startswith("Year ") for h in headers())  # the arrow moved


async def test_help_opens_in_every_mode_and_lists_effective_bindings(app):
    from ptui import ui

    async with app.run_test() as pilot:
        await press(pilot, "?")
        labels = [item.label for item in app.screen.items]
        assert isinstance(app.screen, ui.SelectList)
        assert any(label.startswith("f r") and "relocate" in label for label in labels)
        assert any("(not implemented)" in label for label in labels)  # honest about d d
        await press(pilot, "escape")

        await press(pilot, "4", "?")  # the log mode binds nothing at all
        assert isinstance(app.screen, ui.SelectList)
        assert app.screen.items[0].label.startswith("escape")


async def test_sort_reverse_keeps_the_cursor_on_the_document(app):
    async with app.run_test() as pilot:
        await press(pilot, "S")  # unimplemented in v0: logs, does not crash
        await press(pilot, "j")
        current = app.current["title"]
        await press(pilot, "ctrl+s")
        await settle(pilot)
        assert app.current["title"] == current
