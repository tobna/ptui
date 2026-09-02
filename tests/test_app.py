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
        assert app.query_one("#log-pane").display  # focusing a pane shows it
        await press(pilot, "escape")
        assert app.mode == "list"
        assert not app.query_one("#log-pane").display  # escape closes it, not just unfocuses
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
        table, info, panes = (app.query_one(f"#{name}") for name in ("list-pane", "info-pane", "panes"))
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
        # the flexible column's header is indented past the exception marker,
        # so that `Title` sits over the titles rather than two cells left of them
        assert f"  Title {ui.glyph('sort_asc')}" in headers()
        assert not any(h.strip().startswith("Year ") for h in headers())  # the arrow moved


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


async def test_cmdline_runs_a_command_by_name_and_shows_its_keys(app):
    from ptui import ui

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, ":")
        assert isinstance(app.screen, ui.SelectList)
        relocate = next(i for i in app.screen.items if i.value == "files.relocate")
        assert relocate.hint == "f r"  # the teaching half: the binding beside the name

        await press(pilot, *"mark.all")  # fuzzy completion narrows to one row
        assert [i.value for i in app.screen.shown] == ["mark.all_filtered"]
        await press(pilot, "enter")
        assert len(app.marks) == 3  # it ran, not just closed


async def test_ctrl_p_reaches_the_cmdline(app):
    """Textual's own command palette used to eat this key above `on_key`."""
    from ptui import ui

    async with app.run_test() as pilot:
        await press(pilot, "ctrl+p")
        assert isinstance(app.screen, ui.SelectList)


async def test_sort_reverse_keeps_the_cursor_on_the_document(app):
    async with app.run_test() as pilot:
        await press(pilot, "S")  # unimplemented in v0: logs, does not crash
        await press(pilot, "j")
        current = app.current["title"]
        await press(pilot, "ctrl+s")
        await settle(pilot)
        assert app.current["title"] == current


async def test_theme_picker_lists_textual_themes_and_switches_live(app):
    from textual.widgets import OptionList

    from ptui import ui

    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.theme == "tokyonight-moon"  # the shipped default, LazyVim's own

        await press(pilot, "\\", "t")
        assert isinstance(app.screen, ui.SelectList)
        offered = [item.value for item in app.screen.items]
        assert {"catppuccin-mocha", "gruvbox", "nord", "rose-pine"} <= set(offered)
        assert "tokyonight-moon" in offered  # ours, registered on top of Textual's
        # the picker opens on the theme in use, not on the top of 21 rows
        assert app.screen.query_one(OptionList).highlighted == offered.index("tokyonight-moon")

        await press(pilot, *"gruvbox", "enter")
        await settle(pilot)
        assert app.theme == "gruvbox"


async def test_an_unknown_theme_says_so_instead_of_starting_unreadable(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.apply_theme("no-such-theme")
        assert app.theme == "tokyonight-moon"


async def test_status_bar_shows_the_mode_and_the_counts(app):
    from textual.widgets import Static

    async with app.run_test() as pilot:
        await settle(pilot)
        bar = lambda: app.query_one("#status-bar", Static).content  # the markup  # noqa: E731
        assert " LIST " in bar()
        assert "3 shown / 3 total" in bar()

        await press(pilot, "space")  # a mark adds the marked segment
        assert "1 marked (1 visible)" in bar()

        await press(pilot, "4")  # the mode block follows the focused pane
        assert " LOG " in bar()


async def test_a_document_with_doctor_findings_is_marked_in_the_list(app, monkeypatch):
    from textual.widgets import DataTable

    from ptui import doctor, ui

    broken = doctor.Finding(
        name="key-type",
        path=None,
        msg="year is a str",
        suggestion_cmd="",
        fix_action=None,
        payload="",
        doc=None,
    )
    # `cached` returns None for *not checked*, [] for clean, findings for broken —
    # and only the last of those may draw the glyph.
    seen = {"id0": [broken], "id1": [], "id2": None}
    monkeypatch.setattr(doctor, "cached", lambda doc: seen[doc["papis_id"]])

    async with app.run_test() as pilot:
        await settle(pilot)
        app.refresh_rows()
        table = app.query_one(DataTable)
        flex = next(i for i, (column, _) in enumerate(app._fit) if not column["width"])
        titles = [table.get_row_at(row)[flex].plain for row in range(3)]

        assert titles[0].startswith(f"{ui.glyph('warning')} ")  # findings
        assert titles[1].startswith("  ")  # clean, but still aligned
        assert titles[2].startswith("  ")  # not checked is not the same as broken
        assert titles[0][2:].startswith("Attention")


async def test_the_log_pane_colours_a_record_by_its_level(app):
    from loguru import logger
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "4")  # the pane renders nothing while it is hidden
        pane = app.query_one(RichLog)

        def colours(text: str) -> set[str]:
            """Every foreground colour on the line holding `text`."""
            line = next(strip for strip in pane.lines if text in "".join(s.text for s in strip._segments))
            # Textual normalises a theme colour (`#ff757f` comes back `#FE757F`),
            # so compare against `theme_variables` — the same source the sink
            # reads — and case-insensitively.
            return {s.style.color.name.lower() for s in line._segments if s.style and s.style.color}

        logger.error("everything is on fire")
        logger.info("just so you know")
        await settle(pilot)

        assert app.theme_variables["error"].lower() in colours("everything is on fire")
        # INFO stays plain: the ordinary line is the one that must not shout
        assert app.theme_variables["error"].lower() not in colours("just so you know")
        assert app.theme_variables["warning"].lower() not in colours("just so you know")


async def test_log_line_colours_follow_the_theme(app):
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "4")
        app.log_line("[red]it broke[/] and [yellow]this is odd[/]")
        await settle(pilot)

        line = next(
            strip for strip in app.query_one(RichLog).lines if "it broke" in "".join(s.text for s in strip._segments)
        )
        colours = {s.style.color.name.lower() for s in line._segments if s.style and s.style.color}
        assert app.theme_variables["error"].lower() in colours
        assert app.theme_variables["warning"].lower() in colours
        assert "red" not in colours  # the basic ANSI colour never reaches the pane


async def test_a_log_record_quoting_markup_is_not_swallowed(app):
    from loguru import logger
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "4")
        logger.warning("key [doc[year]] is odd")  # papis quotes values like this
        await settle(pilot)
        pane = app.query_one(RichLog)
        assert any("key [doc[year]] is odd" in "".join(s.text for s in strip._segments) for strip in pane.lines)


async def test_the_info_pane_gives_the_four_meaningful_fields_a_colour(app):
    from ptui import ui

    async with app.run_test() as pilot:
        await settle(pilot)
        # `show` is what `refresh_info` passes: display text, markup-safe
        value = lambda key, raw: app.info_value(key, raw, ui.literal)  # noqa: E731

        tags = value("tags", ["ml", "cv"])
        assert "[$secondary]ml[/]" in tags and "[$secondary]cv[/]" in tags

        assert value("ref", "Vaswani2017") == "[$accent]Vaswani2017[/]"
        assert "$warning" in value("reading_status", "reading")
        assert "$success" in value("reading_status", "read")
        assert "dim" in value("reading_status", "submitted")  # free strings tolerated

        assert value("rating", 3) == (f"[$warning]{ui.glyph('star') * 3}[/][dim]{ui.glyph('star_empty') * 2}[/]")
        assert value("rating", 99).count(ui.glyph("star")) == 5  # clamped, not five hundred

        # anything else is text, and a bracketed one must survive Textual markup
        assert value("title", "Attention [Extended]") == r"Attention \[Extended]"
        assert value("tags", ["[odd]"]) == r"[$secondary]\[odd][/]"


async def test_the_info_pane_renders_a_real_document(app):
    from textual.widgets import Static

    async with app.run_test() as pilot:
        await settle(pilot)
        text = app.query_one("#info", Static).content
        assert "[$primary bold]Attention Is All You Need[/]" in text
        assert "[$accent]Vaswani2017[/]" in text
        assert "[$secondary]test[/]" in text  # the fixture's one tag, as a chip
