"""The `:` line, help, prompt dispatch and the admin verbs: what the command line
lists and runs, what a blank or unknown prompt does, the checks under `\\`,
doctor runs that do not narrow, emptying the trash, and the add flow up to the
form (no network: importers are stubbed)."""

import json

import pytest
from conftest import build_app, press, settle
from cover_helpers import logs

from ptui import actions, commands, fetch, place, ui


async def test_the_command_line_lists_every_command_with_the_keys_of_this_pane_or_the_list(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3", ":")
        hints = {item.value: item.hint for item in app.screen.items}
        assert set(hints) == set(commands.REGISTRY)
        assert hints["files.detach"] == "x"  # the files pane's own key
        assert hints["doc.merge"] == "m m"  # not bound here: where the list binds it
        assert hints["lib.backfill_dates"] == ""  # bound nowhere, still reachable


async def test_the_command_line_can_keep_the_keys_to_itself(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.km.options["show_keys_in_cmdline"] = False
        await press(pilot, ":")
        assert all(item.hint == "" for item in app.screen.items)


async def test_a_command_without_arguments_runs_as_soon_as_it_is_picked(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        await press(pilot, ":", *"mark.clear", "enter")
        assert app.prompt_kind == ""
        assert app.marks == set()


async def test_an_empty_argument_line_keeps_every_default(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, ":", *"nav.down", "enter", "enter")
        assert app.current["papis_id"] == "id1"  # count = 1


@pytest.mark.parametrize(("text", "says"), [("year true extra", "at most 2"), ('"year', "quotation")])
async def test_a_bad_argument_line_is_reported_and_runs_nothing(app, monkeypatch, text, says):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.cmdline_run(app, "sort.by", text)
        assert app.sort_key == "time-added"
        assert any(says in line for line in logged)


@pytest.mark.parametrize("kind", ["attach", "save", "repoint:0", "goto", "mark", "unmark"])
async def test_a_blank_prompt_does_nothing(app, papis_lib, kind):
    before = (papis_lib / "lib" / "doc0" / "info.yaml").read_text()
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.prompt_result(app, kind, "   ")
        assert app.marks == set()
        assert not (papis_lib / "saved.json").exists()
    assert (papis_lib / "lib" / "doc0" / "info.yaml").read_text() == before


async def test_an_unknown_prompt_kind_is_reported(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.prompt_result(app, "frobnicate", "x")
        assert logged == ["[yellow]unhandled prompt 'frobnicate': x[/]"]


async def test_help_spells_arguments_as_keys_toml_does_and_flags_what_is_missing(app, monkeypatch):
    monkeypatch.delitem(commands.REGISTRY, "doc.browse")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "?")
        items = app.screen.items
        assert any(i.value == "visual.start" and i.label.endswith("— unmark=true") for i in items)  # V
        assert next(i for i in items if i.value == "doc.browse").label.endswith("(not implemented)")
        assert all(i.value for i in items)  # no escape row in the list: escape has its own meaning here
        await press(pilot, "enter")
        assert app.current["papis_id"] == "id0"  # browsing only: nothing ran


async def test_the_keymap_check_reports_into_the_log_and_shows_it(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "\\", "k")
        assert logged == ["keymap: 0 problem(s)"]
        assert app.query_one("#log-pane").display


async def test_the_config_check_names_an_unknown_key_and_shows_the_log(papis_lib, monkeypatch):
    app = build_app(papis_lib, "[nonsense]\nkey = 1\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "\\", "c")
        assert "[yellow]unknown config key:[/] nonsense" in logged
        assert app.query_one("#log-pane").display


@pytest.mark.xfail(
    strict=True,
    reason="BUG: \\ c calls files.pdf_root an unknown config key, though the shipped "
    "config.toml tells the user to set it and SPEC names it a managed root",
)
async def test_the_config_check_knows_pdf_root(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "\\", "c")  # the fixture sets pdf_root, as the shipped comment says to
        assert not any("pdf_root" in line for line in logged), logged


@pytest.mark.parametrize("name", ["picker.confirm", "picker.cancel"])
async def test_the_picker_commands_outside_a_picker_only_explain(app, monkeypatch, name):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        app.run_command(name)
        assert "only applies inside a picker" in logged[-1]


# ── doctor ──────────────────────────────────────────────────────────────────


async def test_rechecking_this_document_does_not_narrow_the_list(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "!", "d")
        assert not app.doctor_only
        assert len(app.rows) == 3
        assert any("over 1 document(s)" in line for line in logged)


async def test_doctor_over_marks_scans_only_the_marks(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        app.marks = {"id1"}
        await press(pilot, "!", "!")
        assert any("over 1 document(s)" in line for line in logged)
        assert app.doctor_only


async def test_an_unknown_check_name_is_reported(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.doctor_run(app, "no-such-check", current=True)
        assert any("no such check" in line and "no-such-check" in line for line in logged)


async def test_fix_one_with_nothing_found_opens_no_picker(app, monkeypatch):
    from ptui import doctor

    monkeypatch.setattr(doctor, "scan", lambda doc, names: [])
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "!", "o")
        assert not isinstance(app.screen, ui.SelectList)
        assert logged == ["doctor: nothing to fix here"]


# ── the trash ───────────────────────────────────────────────────────────────


def _trash(papis_lib, *names: str, days_ago: float = 40) -> None:
    """Put a folder and a lone file in the trash, `days_ago`."""
    trash = papis_lib / "trash"
    for name in names:
        source = papis_lib / name
        if name.endswith(".pdf"):
            source.write_bytes(b"x" * 1000)
        else:
            source.mkdir()
            (source / "info.yaml").write_text("title: x\n")
        place.trash(source, trash)
    index = json.loads((trash / place.TRASH_INDEX).read_text())
    (trash / place.TRASH_INDEX).write_text(json.dumps({k: v - days_ago * 86400 for k, v in index.items()}))


async def test_emptying_a_trash_that_does_not_exist_says_so(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.lib_empty_trash(app)
        assert logged == ["the trash is empty"]


async def test_nothing_old_enough_asks_nothing(app, papis_lib, monkeypatch):
    _trash(papis_lib, "fresh", days_ago=1)
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.lib_empty_trash(app)
        assert not isinstance(app.screen, ui.SelectList)
        assert any("older than 30 day(s)" in line for line in logged)
    assert (papis_lib / "trash" / "fresh").exists()


async def test_cancelling_the_purge_keeps_everything(app, papis_lib):
    _trash(papis_lib, "old")
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.lib_empty_trash(app)
        await press(pilot, "down", "enter")
    assert (papis_lib / "trash" / "old").exists()


async def test_purging_takes_folders_and_lone_files_and_a_day_count_overrides(app, papis_lib):
    _trash(papis_lib, "old", "paper.pdf", days_ago=5)
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.cmdline_run(app, "lib.empty_trash", "2")
        assert "0.0 MB" in app.screen.items[0].label  # 1 kB of pdf plus a tiny info.yaml
        await press(pilot, "enter")
    trash = papis_lib / "trash"
    assert sorted(p.name for p in trash.iterdir()) == [place.TRASH_INDEX]
    assert json.loads((trash / place.TRASH_INDEX).read_text()) == {}


# ── adding, up to the form ──────────────────────────────────────────────────


async def test_the_source_picker_offers_files_urls_bib_and_each_importer_once(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "a")
        values = [item.value for item in app.screen.items]
        assert values[:4] == ["", "inbox", "url", "bib"]
        assert "arxiv" in values and "doi" in values
        assert "bibtex" not in values  # the .bib row lists entries instead
        await press(pilot, "enter")  # "a file on disk…"
        assert app.prompt_kind == "add"


@pytest.mark.parametrize(("source", "kind"), [("url", "import:url"), ("bib", "import:bib"), ("doi", "import:doi")])
async def test_each_source_asks_for_what_it_needs(app, source, kind):
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_add(app, source)
        assert app.prompt_kind == kind


async def test_the_inbox_when_there_is_none_says_where_it_looked(app, papis_lib, monkeypatch):
    app.cfg.data["files"]["inbox"] = str(papis_lib / "no-inbox")
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "i")
        assert not isinstance(app.screen, ui.SelectList)
        assert any("inbox not found" in line and "no-inbox" in line for line in logged)


async def test_a_blank_import_fetches_nothing(app, monkeypatch):
    monkeypatch.setattr(fetch, "fetch", lambda *a: pytest.fail("fetched"))
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.prompt_result(app, "import:doi", "  ")
        await app.workers.wait_for_complete()


async def test_a_failed_import_says_why_and_opens_no_form(app, monkeypatch):
    def offline(name, uri):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(fetch, "fetch", offline)
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.prompt_result(app, "import:doi", "10.1/x")
        await app.workers.wait_for_complete()
        await settle(pilot)
        assert any("doi failed" in line and "no route to host" in line for line in logged)
        assert not isinstance(app.screen, ui.AddForm)


async def test_a_url_goes_to_papiss_downloaders(app, monkeypatch):
    asked = []
    monkeypatch.setattr(fetch, "from_url", lambda url: asked.append(url) or ({"title": "From URL"}, []))
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.cmdline_run(app, "doc.add", "url https://example.org/paper")
        await app.workers.wait_for_complete()
        await settle(pilot)
        assert asked == ["https://example.org/paper"]
        assert isinstance(app.screen, ui.AddForm)


async def test_an_unreadable_bib_is_reported(app, papis_lib, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.prompt_result(app, "import:bib", str(papis_lib / "missing.bib"))
        assert any(".bib failed" in line for line in logged)
        assert not isinstance(app.screen, ui.SelectList)
