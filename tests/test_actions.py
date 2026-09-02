from conftest import press, settle

from ptui import actions, clip


def _attach(papis_lib, name: str = "downloaded paper.pdf", body: bytes = b"pdf") -> None:
    """Give doc0 a file, the way a user's script would: appended to `files`."""
    folder = papis_lib / "lib" / "doc0"
    (folder / name).write_bytes(body)
    info = folder / "info.yaml"
    text = info.read_text()
    info.write_text(text + f"- {name}\n" if "files:" in text else text + f"files:\n- {name}\n")


async def test_relocate_moves_files_and_rewrites_info_yaml(app, papis_lib, monkeypatch):
    _attach(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "r")
        await settle(pilot)

    dest = papis_lib / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    assert dest.read_bytes() == b"pdf"
    assert not (papis_lib / "lib" / "doc0" / "downloaded paper.pdf").exists()

    info = (papis_lib / "lib" / "doc0" / "info.yaml").read_text()
    assert str(dest) in info
    assert "papis_id: id0" in info  # the rest of the file survived


async def test_relocate_reports_conflicts_without_touching_anything(app, papis_lib):
    _attach(papis_lib)
    (papis_lib / "pdfs").mkdir()
    clash = papis_lib / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    clash.write_bytes(b"a different paper")

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "r")
        await settle(pilot)

    assert clash.read_bytes() == b"a different paper"
    assert (papis_lib / "lib" / "doc0" / "downloaded paper.pdf").exists()
    assert "files:" in (papis_lib / "lib" / "doc0" / "info.yaml").read_text()


async def test_citekey_yank_is_batch_aware(app, monkeypatch):
    yanked = []
    monkeypatch.setattr(clip, "copy", lambda app, text: yanked.append(text) or "test")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "y", "y")
        assert yanked == ["\\cite{Vaswani2017}"]
        await press(pilot, "space", "space")  # mark two documents
        await press(pilot, "y", "y")
        assert yanked[-1] == "\\cite{Vaswani2017} \\cite{He2016}"


async def test_main_file_comes_before_notes(app, papis_lib):
    _attach(papis_lib, "paper.pdf")
    _attach(papis_lib, "paper_notes.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        names = [p.name for p in actions.files_of(app, app.current)]
    assert names == ["paper.pdf", "paper_notes.pdf"]


async def test_open_reports_a_missing_file_instead_of_launching(app, papis_lib, monkeypatch):
    import papis.utils

    _attach(papis_lib)
    (papis_lib / "lib" / "doc0" / "downloaded paper.pdf").unlink()
    opened = []
    monkeypatch.setattr(papis.utils, "open_file", lambda *a, **k: opened.append(a))
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "o")
    assert opened == []


def test_parse_args_maps_a_typed_line_onto_the_signature():
    import pytest

    from ptui import commands

    assert commands.parse_args("sort.by", "year true") == {"key": "year", "reverse": True}
    assert commands.parse_args("sort.by", "year false") == {"key": "year", "reverse": False}
    assert commands.parse_args("nav.down", "5") == {"count": 5}
    assert commands.parse_args("pane.resize", "0.05") == {"delta": 0.05}
    assert commands.parse_args("export.bibtex", '"/tmp/two words.bib"') == {"target": "/tmp/two words.bib"}
    assert commands.parse_args("sort.by", "") == {}  # defaults stand
    assert commands.signature("sort.by") == "key [reverse]"

    with pytest.raises(ValueError):
        commands.parse_args("sort.reverse", "year")  # takes none
    with pytest.raises(ValueError):
        commands.parse_args("nav.down", "many")  # not a number


async def test_cmdline_prompts_for_arguments_and_passes_them(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, ":", *"sort.by", "enter")
        assert app.prompt_kind == "cmdline:sort.by"  # it needs args, so it asks

        await press(pilot, *"year true", "enter")
        await settle(pilot)
        assert (app.sort_key, app.sort_reverse) == ("year", True)
        assert [d["year"] for d in app.rows] == [2017, 2016, 2003]

        await press(pilot, ":", *"pane.focus", "enter", *"log", "enter")
        assert app.mode == "log"


async def test_cmdline_reports_a_bad_argument_line(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        monkeypatch.setattr(app, "log_line", logged.append)
        await press(pilot, ":", *"nav.down", "enter", *"many", "enter")
        assert app.current["year"] == 2017  # nothing moved, nothing crashed
        assert any("many" in message for message in logged)  # and it said why


async def test_set_writes_a_field_with_the_type_papis_declares(app, papis_lib):
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "f")
        assert app.prompt_kind == "set"
        await press(pilot, *"tags ml, cv", "enter")
        await settle(pilot)
        assert app.current["tags"] == ["ml", "cv"]  # a list, per `tags:list`

        await press(pilot, "c", "f", *"year 2020", "enter")
        await settle(pilot)
        assert app.current["year"] == 2020  # an int, per `year:int`

        await press(pilot, "c", "f", *"tags", "enter")  # no value clears the key
        await settle(pilot)
        assert "tags" not in app.current

    text = info.read_text()
    assert "papis_id: id0" in text  # the rest of the file survived the writes
    assert "year: 2020" in text


async def test_set_refuses_a_value_of_the_wrong_type(app, papis_lib, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        monkeypatch.setattr(app, "log_line", logged.append)
        await press(pilot, "c", "f", *"year twenty", "enter")
        await settle(pilot)
        assert app.current["year"] == 2017  # nothing written
        assert any("must be int" in message for message in logged)


async def test_set_over_marks_asks_first(app):
    from ptui import ui

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space", "space")  # mark two documents
        await press(pilot, "c", "f", *"reading_status read", "enter")
        assert isinstance(app.screen, ui.SelectList)  # a batch confirms

        await press(pilot, "escape")  # ...and cancelling writes nothing
        await settle(pilot)
        assert not any(d.get("reading_status") for d in app.docs)

        await press(pilot, "c", "f", *"reading_status read", "enter", "enter")
        await settle(pilot)
        assert [d.get("reading_status") for d in app.targets] == ["read", "read"]


async def test_tag_adds_and_untag_removes_without_touching_the_rest(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.current["tags"] == ["test"]  # the fixture's own tag
        await press(pilot, "c", "t")
        assert app.prompt_kind == "tag"
        await press(pilot, *"ml, cv", "enter")
        await settle(pilot)
        assert app.current["tags"] == ["test", "ml", "cv"]  # added, not replaced

        await press(pilot, "c", "t", *"ml", "enter")  # already there
        await settle(pilot)
        assert app.current["tags"] == ["test", "ml", "cv"]  # no duplicate

        await press(pilot, "c", "T", *"test cv", "enter")
        await settle(pilot)
        assert app.current["tags"] == ["ml"]

        await press(pilot, "c", "T", *"ml", "enter")  # the last one goes with the key
        await settle(pilot)
        assert "tags" not in app.current

    assert "papis_id: id0" in (papis_lib / "lib" / "doc0" / "info.yaml").read_text()


async def test_status_and_rating_pick_a_value(app):
    from ptui import ui

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "s")
        assert isinstance(app.screen, ui.SelectList)
        await press(pilot, *"reading", "enter")
        await settle(pilot)
        assert app.current["reading_status"] == "reading"

        await press(pilot, "c", "r", "down", "down", "down", "enter")  # 0,1,2 -> 3
        await settle(pilot)
        assert app.current["rating"] == 3
        assert isinstance(app.current["rating"], int)  # never the string "3"

        await press(pilot, "c", "r", "up", "up", "up", "enter")  # opens on 3; 0 clears
        await settle(pilot)
        assert "rating" not in app.current

        await press(pilot, "c", "s", *"clear", "enter")
        await settle(pilot)
        assert "reading_status" not in app.current


async def test_tagging_a_batch_confirms_and_keeps_each_documents_own_tags(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "t", *"solo", "enter")  # doc0 only
        await settle(pilot)
        await press(pilot, "space", "space")  # mark doc0 and doc1
        await press(pilot, "c", "t", *"shared", "enter", "enter")  # confirm the batch
        await settle(pilot)
        assert [d["tags"] for d in app.targets] == [
            ["test", "solo", "shared"],
            ["test", "shared"],
        ]


async def test_edit_reports_yaml_it_can_no_longer_parse(app, papis_lib, monkeypatch):
    import contextlib

    import papis.commands.edit

    info = papis_lib / "lib" / "doc0" / "info.yaml"
    monkeypatch.setattr(papis.commands.edit, "run", lambda doc, **kw: info.write_text("title: [unclosed\n"))
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        monkeypatch.setattr(app, "log_line", logged.append)
        # $EDITOR takes the terminal for real, which a headless pilot has not got
        monkeypatch.setattr(app, "suspend", contextlib.nullcontext)
        await press(pilot, "e")  # edit.mode defaults to editor, so `e` is `E`
        await settle(pilot)
        assert any("invalid YAML" in message for message in logged)
        assert app.current["title"] == "Attention Is All You Need"  # not reloaded


async def test_backfill_stamps_only_the_documents_that_lack_time_added(app, papis_lib):
    """papis stopped writing the key, so most of a real library has none and the
    "recently added" sort has nothing to order it by."""
    from ptui import library, ui

    info = papis_lib / "lib" / "doc0" / "info.yaml"
    info.write_text(info.read_text().replace("time-added: 2024-01-01-00:00:00\n", ""))

    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.docs[-1]["ref"] == "Vaswani2017"  # no key: sorted into the tail

        mtime = info.stat().st_mtime  # the write moves it, so read it first
        actions.lib_backfill_dates(app)
        await pilot.pause()
        assert isinstance(app.screen, ui.SelectList)  # it confirms
        await press(pilot, "enter")
        await settle(pilot)

        stamped = {d["ref"]: d[library.TIME_ADDED] for d in app.docs}
        assert stamped["Vaswani2017"] == library.stamp(mtime)
        assert stamped["He2016"] == "2023-01-01-00:00:00"  # untouched
        assert app.docs[0]["ref"] == "Vaswani2017"  # and the list re-sorted
