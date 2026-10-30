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
        assert any(label.startswith("! d") and "current=true" in label for label in labels)  # not a bare True
        await press(pilot, "escape")

        await press(pilot, "4", "?")  # the log mode binds only the pane keys
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
        await press(pilot, "enter", "enter")  # `[unmark]` gets the prompt; empty keeps the default
        assert len(app.marks) == 3  # it ran, not just closed


async def test_ctrl_p_reaches_the_cmdline(app):
    """Textual's own command palette used to eat this key above `on_key`."""
    from ptui import ui

    async with app.run_test() as pilot:
        await press(pilot, "ctrl+p")
        assert isinstance(app.screen, ui.SelectList)


async def test_sort_reverse_keeps_the_cursor_on_the_document(app):
    """Row 0 moves to the bottom when the sort flips; the cursor goes with it.
    (The middle row of three maps onto itself and would prove nothing.)"""
    async with app.run_test() as pilot:
        await settle(pilot)
        current = app.current["title"]
        await press(pilot, "ctrl+s")
        assert app.current["title"] == current
        assert app.query_one("DataTable").cursor_row == 2


async def test_clearing_the_narrow_keeps_the_cursor_on_the_document(app):
    async with app.run_test() as pilot:
        await press(pilot, "/", "n", "e", "u", "r", "a", "l", "enter")
        assert app.current["title"] == "A Neural Probabilistic Language Model"
        await press(pilot, "escape")
        assert len(app.rows) == 3
        assert app.current["title"] == "A Neural Probabilistic Language Model"


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
        name="field-type",
        path=None,
        msg="year is a str",
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


async def test_a_logged_value_in_brackets_survives(app):
    """Every `log_line` call interpolates something — a config path, a title, a
    keymap section — and Rich read `[modes.files]` or `[ui]` as a tag and ate it."""
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await press(pilot, "4")
        app.log_line("[yellow]keys.toml:[/] [modes.files] 'x' under [ui] for [dim]dim[/] [weird].pdf")
        await settle(pilot)
        text = app.query_one(RichLog).lines[-1].text
        assert text == "keys.toml: [modes.files] 'x' under [ui] for dim [weird].pdf"


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
        text = app.query_one("#info", Static).content.markup
        assert "[$primary bold]Attention Is All You Need[/$primary bold]" in text
        assert "[$accent]Vaswani2017[/$accent]" in text
        assert "[$secondary]test[/$secondary]" in text  # the fixture's one tag, as a chip


async def test_a_long_info_value_wraps_under_itself_not_under_its_label(app):
    from textual.widgets import Static

    from ptui import actions

    async with app.run_test(size=(80, 30)) as pilot:
        await settle(pilot)
        actions.doc_set(app, "url", "https://example.org/" + "very-long-path-segment/" * 6)
        await settle(pilot)
        lines = app.query_one("#info", Static).content.plain.splitlines()
        head = next(i for i, line in enumerate(lines) if "url" in line and "https" in line)
        indent = lines[head].index("https")
        assert lines[head + 1].startswith(" " * indent)
        assert lines[head + 1].strip()  # the continuation, not a blank line


async def test_a_missing_nested_key_renders_blank_without_a_papis_warning(app):
    """`default=""` made papis log a warning *with a traceback* for every such
    cell on every redraw — and build the document's description to do it."""
    import papis.document
    import papis.format
    from loguru import logger

    doc = papis.document.from_data({"title": "No authors"})
    column = {"title": "Author", "format": "{doc[author_list][0][family]}", "width": 18}
    async with app.run_test():
        seen: list[str] = []
        logger.add(seen.append, format="{message}")  # papis's records arrive through loguru
        assert app.cell_text(doc, column) == ""
        papis.format.format(column["format"], doc, default="")  # the old call, for contrast
    assert len(seen) == 1
    assert "Could not format" in seen[0]


async def test_papis_records_reach_the_log_pane_not_the_terminal(app):
    """Under `papis ptui` papis's StreamHandler held the real stderr and wrote over
    the screen; standalone, its warnings went nowhere at all."""
    import logging

    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await settle(pilot)
        papis_log = logging.getLogger("papis")
        assert not [h for h in papis_log.handlers if isinstance(h, logging.StreamHandler)]
        logging.getLogger("papis.downloaders").warning("could not fetch [the thing]")
        logging.getLogger("papis.add").info("chatter")
        await press(pilot, "4")
        lines = [line.text for line in app.query_one(RichLog).lines]
        assert any("papis.downloaders: could not fetch [the thing]" in line for line in lines)
        assert not any("chatter" in line for line in lines)  # INFO stays in the file


async def test_an_edit_shows_up_in_the_cached_cells(app):
    from ptui import actions

    title = next(column for column in app.cfg.get("list.columns") if column["title"] == "Title")
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.cell_text(app.current, title) == "Attention Is All You Need"
        actions.doc_set(app, "title", "Renamed")
        assert app.cell_text(app.current, title) == "Renamed"


async def test_marking_redraws_only_the_row_it_changed(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        table = app.query_one("DataTable")
        first = str(table.get_row_at(0)[0])
        await press(pilot, "space")
        assert str(table.get_row_at(0)[0]) != first  # the mark glyph is there
        assert app.query_one("DataTable").cursor_row == 1  # and the cursor advanced


async def test_the_status_bar_shows_a_bracketed_query_as_typed(app):
    async with app.run_test() as pilot:
        await press(pilot, "/", "[", "x", "]", "enter")
        assert "/ [x]" in str(app.query_one("#status-bar").render())


async def test_a_failed_scope_keeps_the_scope_that_is_on_screen(app):
    """papis rejects `year:>2020`; the status bar used to name it anyway, over
    the previous query's results."""
    async with app.run_test() as pilot:
        await press(pilot, "s", *"year:2016", "enter")
        assert len(app.docs) == 1
        await press(pilot, "s", "ctrl+u", *"year:>2020", "enter")
        assert app.scope_query == "year:2016"
        assert len(app.docs) == 1
        assert app.query_one("#log-pane").display


async def test_the_pane_keys_work_from_every_pane(app):
    """`[modes.info]` bound only j/k/e and the log had no table at all, so `tab`
    stopped at the info pane and `g o` could not close the log it opened."""
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "tab")
        assert app.mode == "info"
        await press(pilot, "tab")
        assert app.mode == "files"
        await press(pilot, "tab")
        assert app.mode == "log"
        assert app.query_one("#log-pane").display
        await press(pilot, "g", "o")
        assert app.mode == "list"
        assert not app.query_one("#log-pane").display
        await press(pilot, "2", "1")
        assert app.mode == "list"


async def test_a_failing_command_is_reported_once(app, monkeypatch):
    from textual.widgets import RichLog

    from ptui import commands

    def boom(_app):
        raise RuntimeError("kaputt")

    monkeypatch.setitem(commands.REGISTRY, "test.boom", commands.Command("test.boom", "explode", boom))
    async with app.run_test() as pilot:
        await press(pilot, "4")
        app.run_command("test.boom")
        await settle(pilot)
        lines = [line.text for line in app.query_one(RichLog).lines]
        assert sum("test.boom failed" in line for line in lines) == 1
        assert any("kaputt" in line for line in lines)


async def test_a_long_log_line_wraps_instead_of_being_cut(app):
    from textual.widgets import RichLog

    async with app.run_test(size=(60, 30)) as pilot:
        await press(pilot, "4")
        app.log_line("file " + "/very/long/path" * 8 + " declared but does not exist")
        await settle(pilot)
        log = app.query_one(RichLog)
        assert "does not exist" in "".join(line.text for line in log.lines)
        assert max(line.cell_length for line in log.lines) <= log.size.width


async def test_two_flexible_columns_share_the_room(papis_lib):
    """A second `width = 0` column used to be squeezed to nothing and vanish."""
    from conftest import build_app

    app = build_app(papis_lib)
    app.cfg.data["list"]["columns"] = [
        {"title": "Author", "format": "{doc[author]}", "width": 0},
        {"title": "Title", "format": "{doc[title]}", "width": 0},
    ]
    async with app.run_test(size=(100, 24)) as pilot:
        await settle(pilot)
        widths = {column["title"]: width for column, width in app._fit}
        assert set(widths) == {"Author", "Title"}
        assert abs(widths["Author"] - widths["Title"]) <= 1
        row = [str(cell) for cell in app.query_one("DataTable").get_row_at(0)]
        assert "Vaswani" in row[0]
        assert "Attention" in row[1]


async def test_the_first_header_sits_over_its_values_not_over_the_mark(papis_lib):
    from conftest import build_app

    app = build_app(papis_lib)
    app.cfg.data["list"]["columns"] = [
        {"title": "Year", "format": "{doc[year]}", "width": 6},
        {"title": "Title", "format": "{doc[title]}", "width": 0},
    ]
    async with app.run_test(size=(100, 24)) as pilot:
        await settle(pilot)
        header = str(app.query_one("DataTable").ordered_columns[0].label)
        cell = str(app.query_one("DataTable").get_row_at(0)[0])
        assert header.index("Year") == cell.index("2017")


async def test_a_startup_query_papis_rejects_opens_the_whole_library(papis_lib):
    from conftest import build_app

    app = build_app(papis_lib)
    app.scope_query = "year:>2000"
    async with app.run_test() as pilot:
        await settle(pilot)
        assert len(app.docs) == 3
        assert app.scope_query == ""


async def test_an_unknown_library_is_one_readable_sentence(app):
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await press(pilot, "4")
        app.run_command("lib.switch", {"name": "no-such-library"})
        await settle(pilot)
        text = " ".join(" ".join(line.text for line in app.query_one(RichLog).lines).split())
        assert "Library 'no-such-library' does not seem to exist. To add a library" in text
        assert "('" not in text


def _give_files(papis_lib, *names, doc="doc0"):
    folder = papis_lib / "lib" / doc
    for name in names:
        (folder / name).write_bytes(name.encode())
    info = folder / "info.yaml"
    info.write_text(info.read_text() + "files:\n" + "".join(f"- {name}\n" for name in names))


async def test_the_files_pane_lists_the_entries_and_opens_the_one_under_its_cursor(app, papis_lib, monkeypatch):
    import papis.utils
    from textual.widgets import OptionList

    _give_files(papis_lib, "paper.pdf", "paper_supp.pdf")
    opened = []
    monkeypatch.setattr(papis.utils, "open_file", lambda path, **_: opened.append(path))
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3")
        pane = app.query_one("#files-pane", OptionList)
        assert app.mode == "files" and pane.display
        assert [str(pane.get_option_at_index(i).prompt).split()[0] for i in range(2)] == ["paper.pdf", "paper_supp.pdf"]
        await press(pilot, "j")
        assert app.file_index == 1
        assert app.current["title"] == "Attention Is All You Need"  # j moved the file, not the document
        await press(pilot, "enter")
        assert opened[-1].endswith("paper_supp.pdf")
        await press(pilot, "escape")
        assert not pane.display  # transient, like the log


async def test_detach_keeps_the_file_and_reorder_moves_the_cursor_with_it(app, papis_lib):
    _give_files(papis_lib, "a.pdf", "b.pdf", "c.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3", "J")  # a.pdf down one
        assert app.current["files"] == ["b.pdf", "a.pdf", "c.pdf"]
        assert app.file_index == 1
        await press(pilot, "x")  # detach a.pdf
        assert app.current["files"] == ["b.pdf", "c.pdf"]
        assert (papis_lib / "lib" / "doc0" / "a.pdf").exists()
        await press(pilot, "u")
        assert app.current["files"] == ["b.pdf", "a.pdf", "c.pdf"]
        await press(pilot, "u")
        assert app.current["files"] == ["a.pdf", "b.pdf", "c.pdf"]


async def test_repoint_offers_the_folder_files_no_entry_holds(app, papis_lib):
    _give_files(papis_lib, "kept.pdf", "gone.pdf")
    folder = papis_lib / "lib" / "doc0"
    (folder / "gone.pdf").rename(folder / "renamed.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3", "j", "p")
        assert [item.label for item in app.screen.items] == ["a path…", "renamed.pdf"]
        await press(pilot, "down", "enter")
        assert app.current["files"] == ["kept.pdf", "renamed.pdf"]  # relative, like the entry it replaced
        await press(pilot, "u")
        assert app.current["files"] == ["kept.pdf", "gone.pdf"]


async def test_rename_uses_papis_scheme_and_undoes(app, papis_lib):
    import papis.config

    _give_files(papis_lib, "paper.pdf", "2017-vaswani.pdf")
    folder = papis_lib / "lib" / "doc0"
    async with app.run_test() as pilot:
        await settle(pilot)
        papis.config.set("add-file-name", "")
        await press(pilot, "3", "r")
        assert (folder / "paper.pdf").exists()  # no scheme: nothing renamed

        papis.config.set("add-file-name", "{doc[year]}-{doc[author_list][0][family]}")
        await press(pilot, "r")
        # the plain name is taken by the second file, so papis's suffix kicks in
        assert app.current["files"][0] == "2017-vaswani-a.pdf"
        assert (folder / "2017-vaswani-a.pdf").exists()
        assert not (folder / "paper.pdf").exists()
        await press(pilot, "u")
        assert app.current["files"][0] == "paper.pdf"
        assert (folder / "paper.pdf").exists()


async def test_rename_leaves_a_supplement_its_name(app, papis_lib):
    import papis.config

    _give_files(papis_lib, "paper_supp.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        papis.config.set("add-file-name", "{doc[year]}-{doc[author_list][0][family]}")
        await press(pilot, "3", "r")
        assert app.current["files"] == ["paper_supp.pdf"]
