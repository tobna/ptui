"""Scope, narrow and sort state, saved searches, and libraries.

Switching or moving between libraries included: the state every view is built from.
"""

import json
import os

import pytest
from conftest import build_app, press, settle
from cover_helpers import logs

from ptui import actions, library, ui


def _ids(docs) -> list[str]:
    """Reduce documents to their ids.

    Args:
        docs: The documents.

    Returns:
        Their ids, in order.
    """
    return [library.doc_id(doc) for doc in docs]


async def test_an_alias_reaches_papis_expanded_and_the_status_keeps_what_was_typed(app, monkeypatch):
    """An alias reaches papis expanded and the status keeps what was typed.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        monkeypatch: pytest's monkeypatch fixture.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        assert actions.scope(app, "a:He")
        assert _ids(app.docs) == ["id1"]
        assert app.scope_query == "a:He"
        assert any("author:He" in line for line in logged)  # says what papis was asked


async def test_a_new_scope_keeps_the_narrow_on_top_of_it(app):
    """A new scope keeps the narrow on top of it.

    Args:
        app: The `PtuiApp` fixture, not started yet.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "/", *"deep", "enter")
        await settle(pilot)
        actions.scope(app, "year:2016 OR year:2017")
        assert app.narrow_query == "deep"
        assert _ids(app.rows) == ["id1"]


async def test_clearing_the_narrow_keeps_the_scope(app):
    """Clearing the narrow keeps the scope.

    Args:
        app: The `PtuiApp` fixture, not started yet.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.scope(app, "year:2016 OR year:2017")
        actions.query_narrow(app, "deep")
        await settle(pilot)
        assert _ids(app.rows) == ["id1"]
        actions.query_clear(app)
        assert app.scope_query == "year:2016 OR year:2017"
        assert _ids(app.rows) == ["id0", "id1"]


@pytest.mark.parametrize(
    ("command", "kind", "value"),
    [(actions.query_scope, "scope", "year:2016"), (actions.query_narrow, "narrow", "deep")],
)
async def test_the_query_prompts_open_on_what_is_already_applied(app, command, kind, value):
    """The query prompts open on what is already applied.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        command: Parametrized, such as `actions.query_scope`.
        kind: Parametrized, such as `'scope'`.
        value: Parametrized, such as `'year:2016'`.
    """
    from textual.widgets import Input

    async with app.run_test() as pilot:
        await settle(pilot)
        command(app, value)
        await settle(pilot)
        command(app)
        assert app.prompt_kind == kind
        assert app.query_one(Input).value == value  # edited, not retyped


@pytest.mark.parametrize(("key", "reverse"), [("year", True), ("title", False), ("volume", False)])
async def test_sort_by_takes_the_presets_direction_and_ascending_for_any_other_key(app, key, reverse):
    """Sort by takes the presets direction and ascending for any other key.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        key: Parametrized, such as `'year'`.
        reverse: Parametrized, such as `True`.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.sort_by(app, key)
        assert (app.sort_key, app.sort_reverse) == (key, reverse)


async def test_sorting_keeps_the_narrow_and_the_cursor(app):
    """SPEC: changing the sort must not clear the narrow, and the cursor stays on the same document.

    Args:
        app: The `PtuiApp` fixture, not started yet.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "/", *"year:>2010", "enter")
        await settle(pilot)
        await press(pilot, "j")
        at = library.doc_id(app.current)
        actions.sort_by(app, "year", reverse=False)
        assert app.narrow_query == "year:>2010"
        assert _ids(app.rows) == ["id1", "id0"]
        assert library.doc_id(app.current) == at
        await press(pilot, "ctrl+s")
        assert _ids(app.rows) == ["id0", "id1"]
        assert library.doc_id(app.current) == at


async def test_the_sort_picker_offers_presets_then_discovered_keys_without_repeats(app):
    """The sort picker offers presets then discovered keys without repeats.

    Args:
        app: The `PtuiApp` fixture, not started yet.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "S")
        items = app.screen.items
        presets = [item.value for item in items if item.hint != "discovered"]
        discovered = [item.value for item in items if item.hint == "discovered"]
        assert presets[0] == "time-added"
        assert "ref" in discovered and "papis_id" in discovered
        assert not set(presets) & set(discovered)  # a preset key is not listed twice

        await press(pilot, *"ref", "shift+enter")  # a discovered key ascends; shift inverts
        assert (app.sort_key, app.sort_reverse) == ("ref", True)
        assert _ids(app.rows) == ["id0", "id1", "id2"]  # Vaswani, He, Bengio


async def test_sort_discovery_can_be_turned_off(papis_lib):
    """Sort discovery can be turned off.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    app = build_app(papis_lib, "[list]\nsort_discover_keys = false\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "S")
        assert all(item.hint != "discovered" for item in app.screen.items)


# ── saved searches ──────────────────────────────────────────────────────────


async def test_a_plain_string_in_config_is_a_scope_that_keeps_the_sort(papis_lib):
    """A plain string in config is a scope that keeps the sort.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    app = build_app(papis_lib, '[query.saved]\nold = "year:2003"\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.sort_by(app, "title")
        actions.query_load(app, "old")
        assert _ids(app.docs) == ["id2"]
        assert (app.sort_key, app.sort_reverse) == ("title", False)


async def test_a_table_in_config_brings_its_sort(papis_lib):
    """A table in config brings its sort.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    app = build_app(papis_lib, '[query.saved.recent]\nscope = ""\nsort = "year"\nreverse = false\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.query_load(app, "recent")
        assert app.sort_key == "year"
        assert _ids(app.rows) == ["id2", "id1", "id0"]


async def test_saving_under_a_config_name_replaces_it_and_says_so(app, papis_lib, monkeypatch):
    """Saving under a config name replaces it and says so.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        monkeypatch: pytest's monkeypatch fixture.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.scope(app, "year:2016")
        logged = logs(app, monkeypatch)
        actions.query_save(app, "to-read")  # the shipped [query.saved] entry
        assert any(line.startswith("replaced to-read") for line in logged)
        assert actions.saved_searches(app)["to-read"]["scope"] == "year:2016"  # the app's wins


async def test_a_search_from_config_cannot_be_forgotten_from_the_picker(app, papis_lib, monkeypatch):
    """A search from config cannot be forgotten from the picker.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        monkeypatch: pytest's monkeypatch fixture.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "g", "s", *"to-read", "shift+enter")
        assert any("remove it there" in line for line in logged)
        assert "to-read" in actions.saved_searches(app)
        assert not (papis_lib / "saved.json").exists()  # nothing written


async def test_loading_an_unknown_search_changes_nothing(app, monkeypatch):
    """Loading an unknown search changes nothing.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        monkeypatch: pytest's monkeypatch fixture.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.scope(app, "year:2016")
        logged = logs(app, monkeypatch)
        actions.query_load(app, "nope")
        assert app.scope_query == "year:2016"
        assert any("no saved search nope" in line for line in logged)


async def test_with_no_saved_searches_the_picker_does_not_open(papis_lib, monkeypatch):
    """With no saved searches the picker does not open.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        monkeypatch: pytest's monkeypatch fixture.
    """
    app = build_app(papis_lib)
    app.config.data["query"]["saved"] = {}
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "g", "s")
        assert not isinstance(app.screen, ui.SelectList)
        assert any("no saved searches" in line for line in logged)


async def test_an_unreadable_saved_file_reads_as_empty(app, papis_lib):
    """An unreadable saved file reads as empty.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    (papis_lib / "saved.json").write_text("{not json")
    async with app.run_test() as pilot:
        await settle(pilot)
        assert set(actions.saved_searches(app)) == {"to-read"}


async def test_a_saved_search_with_unicode_round_trips(app, papis_lib):
    """A saved search with unicode round trips.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.scope(app, "title:Résumé")
        actions.query_save(app, "über")
        assert json.loads((papis_lib / "saved.json").read_text())["über"]["scope"] == "title:Résumé"
        actions.scope(app, "")
        actions.query_load(app, "über")
        assert app.scope_query == "title:Résumé"


async def test_saving_with_no_saved_file_is_reported_not_fatal(papis_lib, monkeypatch):
    """Saving with no saved file is reported not fatal.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        monkeypatch: pytest's monkeypatch fixture.
    """
    app = build_app(papis_lib)
    app.config.data["query"]["saved_file"] = ""
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "\\", "s", *"mine", "enter")
        assert app.is_running
        assert any("saved_file" in line for line in logged)


def test_the_history_keeps_the_newest_500(app, papis_lib):
    """The history keeps the newest 500.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    (papis_lib / "history").write_text("".join(f"q{number}\n" for number in range(600)))
    actions.remember(app, "last")
    past = actions.history(app)
    assert len(past) == actions.HISTORY_MAX
    assert past[-1] == "last"
    assert past[0] == "q101"


def test_a_blank_query_is_not_remembered(app, papis_lib):
    """A blank query is not remembered.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    actions.remember(app, "   ")
    assert not (papis_lib / "history").exists()


# ── libraries ───────────────────────────────────────────────────────────────


async def test_switching_to_an_unknown_library_leaves_everything_as_it_was(app, monkeypatch):
    """Switching to an unknown library leaves everything as it was.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        monkeypatch: pytest's monkeypatch fixture.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0"}
        actions.cmdline_run(app, "lib.switch", "no-such-library")
        await settle(pilot)
        assert app.marks == {"id0"}
        assert len(app.docs) == 3
        assert not app.config.get("general.library")


async def test_switching_libraries_drops_the_marks_of_the_old_one(app, papis_lib):
    """Switching libraries drops the marks of the old one.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    other = papis_lib / "other"
    other.mkdir()
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0"}
        actions.lib_switch(app, str(other))
        assert app.marks == set()
        assert app.docs == []


async def test_the_library_picker_lists_papis_libraries_and_opens_on_the_current(app):
    """The library picker lists papis libraries and opens on the current.

    Args:
        app: The `PtuiApp` fixture, not started yet.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "g", "l")
        assert isinstance(app.screen, ui.SelectList)
        assert [item.value for item in app.screen.items] == ["test"]
        assert app.screen.current == "test"
        await press(pilot, "enter")
        assert len(app.docs) == 3


async def test_moving_with_no_other_library_says_so(app, monkeypatch):
    """Moving with no other library says so.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        monkeypatch: pytest's monkeypatch fixture.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.doc_move(app)
        assert not isinstance(app.screen, ui.SelectList)
        assert any("no other library" in line for line in logged)


async def test_moving_to_an_unknown_library_moves_nothing(app, papis_lib, monkeypatch):
    """Moving to an unknown library moves nothing.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        monkeypatch: pytest's monkeypatch fixture.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.doc_move(app, "no-such-library")
        assert len(app.docs) == 3
        assert any("library no-such-library" in line for line in logged)
    assert (papis_lib / "lib" / "doc0" / "info.yaml").exists()


async def test_cancelling_a_batch_move_moves_nothing(app, papis_lib):
    """Cancelling a batch move moves nothing.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    other = papis_lib / "other"
    other.mkdir()
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        actions.doc_move(app, str(other))
        assert isinstance(app.screen, ui.SelectList)
        await press(pilot, "down", "enter")  # "cancel"
        assert len(app.docs) == 3
        assert app.marks == {"id0", "id1"}
    assert not any(other.iterdir())


async def test_moving_one_document_needs_no_confirmation(app, papis_lib):
    """Moving one document needs no confirmation.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    other = papis_lib / "other"
    other.mkdir()
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_move(app, str(other))
        await settle(pilot)
        assert _ids(app.docs) == ["id1", "id2"]
    assert (other / "doc0" / "info.yaml").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only folder")
async def test_a_history_that_cannot_be_written_costs_the_history_not_the_query(app, papis_lib, monkeypatch):
    """A history that cannot be written costs the history not the query.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        monkeypatch: pytest's monkeypatch fixture.
    """
    state = papis_lib / "state"
    state.mkdir()
    app.config.data["query"]["history_file"] = str(state / "history")
    state.chmod(0o555)
    try:
        async with app.run_test() as pilot:
            await settle(pilot)
            logged = logs(app, monkeypatch)
            await press(pilot, "s", *"year:2016", "enter")
            assert _ids(app.docs) == ["id1"]
            assert any("query history not saved" in line for line in logged)
    finally:
        state.chmod(0o755)


async def test_a_session_state_that_cannot_be_written_does_not_stop_the_app_closing(app, papis_lib):
    """A session state that cannot be written does not stop the app closing.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    (papis_lib / "blocker").write_text("a file where a folder should be")
    app.config.data["general"]["persist_state"] = True
    app.config.data["general"]["state_file"] = str(papis_lib / "blocker" / "state.json")
    async with app.run_test() as pilot:
        await settle(pilot)
    assert app.return_code in (0, None)
    assert (papis_lib / "blocker").read_text() == "a file where a folder should be"
