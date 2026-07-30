from conftest import press, settle
from textual.widgets import OptionList

from ptui import ui


def test_item_filtering_is_fuzzy_over_label_and_hint():
    item = ui.Item(label="First author", value="author_list.0.family", hint="author_list.0.family")
    assert item.matches("")
    assert item.matches("fauth")
    assert item.matches("0.fam")
    assert not item.matches("zzz")


def test_glyphs_switch_with_the_setting_and_stay_one_cell():
    from rich.cells import cell_len

    try:
        ui.use_icons(False)
        assert ui.glyph("mark") == "*"
        ui.use_icons(True)
        assert ui.glyph("mark") == ""
        # column arithmetic assumes it: a two-cell nerd glyph would overflow rows
        assert all(cell_len(ascii_) == cell_len(nerd) == 1 for ascii_, nerd in ui.GLYPHS.values())
    finally:
        ui.use_icons(False)


async def test_icons_setting_reaches_the_list_and_the_status_bar(app):
    from textual.widgets import Static

    app.cfg.data["ui"]["icons"] = True
    async with app.run_test() as pilot:  # __init__ already ran, so set it by hand
        ui.use_icons(True)
        await press(pilot, "space")  # mark the first row
        await settle(pilot)
        assert ui.glyph("sort_desc") in str(app.query_one("#status-bar", Static).content)
        assert ui.glyph("mark") in str(app.query_one("ListTable").get_row_at(0)[0])
    ui.use_icons(False)


async def test_sort_picker_applies_the_keys_own_direction(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "S")
        picker = app.screen
        assert isinstance(picker, ui.SelectList)
        assert app.sort_key == "time-added"

        await press(pilot, "y", "e", "a", "r")  # fuzzy-filter down to "Year ↓"
        await press(pilot, "enter")
        await settle(pilot)
        assert (app.sort_key, app.sort_reverse) == ("year", True)  # the key's own default
        assert [d["year"] for d in app.rows] == [2017, 2016, 2003]


async def test_sort_picker_escape_changes_nothing(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        before = (app.sort_key, app.sort_reverse)
        await press(pilot, "S")
        await press(pilot, "escape")
        await settle(pilot)
        assert (app.sort_key, app.sort_reverse) == before
        assert len(app.screen_stack) == 1


async def test_picker_marks_the_current_value(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "S")
        options = app.screen.query_one(OptionList)
        prompt = str(options.get_option_at_index(options.highlighted).prompt)
        assert ui.glyph("cursor") in prompt  # the cursor sits on the highlighted row

        await press(pilot, "down")  # and follows it
        options = app.screen.query_one(OptionList)
        assert ui.glyph("cursor") not in str(options.get_option_at_index(0).prompt)
        assert ui.glyph("cursor") in str(options.get_option_at_index(1).prompt)
