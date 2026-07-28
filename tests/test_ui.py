from conftest import press, settle
from textual.widgets import OptionList

from ptui import ui


def test_item_filtering_is_fuzzy_over_label_and_hint():
    item = ui.Item(label="First author", value="author_list.0.family", hint="author_list.0.family")
    assert item.matches("")
    assert item.matches("fauth")
    assert item.matches("0.fam")
    assert not item.matches("zzz")


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
        assert ">" in str(options.get_option_at_index(options.highlighted).prompt)
