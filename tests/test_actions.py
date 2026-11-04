from pathlib import Path

import pytest
from conftest import press, settle

from ptui import actions, clip, fetch


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


async def test_relocate_writes_what_moved_when_a_later_entry_raises(app, papis_lib, monkeypatch):
    """place() raising on one entry used to abandon the document after the
    earlier entries had moved, leaving info.yaml naming paths that were gone."""
    from ptui import place

    _attach(papis_lib, "a.pdf")
    _attach(papis_lib, "b.djvu", b"djvu")
    real = place.place

    def flaky(doc, entry, *args, **kwargs):
        if str(entry).endswith(".djvu"):
            raise OSError("the disk said no")
        return real(doc, entry, *args, **kwargs)

    monkeypatch.setattr(place, "place", flaky)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "r")
        await settle(pilot)
        files = app.current["files"]
        assert files[1] == "b.djvu"
        assert all(place.resolve(app.current, entry).exists() for entry in files), files
        assert Path(files[0]).parent == papis_lib / "pdfs"


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


async def test_undoing_a_relocate_puts_file_and_entry_back_and_redo_moves_them_again(app, papis_lib):
    """`f r` pushed no undo step, so `u` right after it undid the tag before it
    and left the file in the PDF root."""
    _attach(papis_lib)
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    src = papis_lib / "lib" / "doc0" / "downloaded paper.pdf"
    dest = papis_lib / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_tag(app, "kept")
        tagged = info.read_text()
        await press(pilot, "f", "r")  # one document: no question
        assert dest.read_bytes() == b"pdf" and not src.exists()

        await press(pilot, "u")
        assert src.read_bytes() == b"pdf" and not dest.exists()
        assert info.read_text() == tagged  # the entry back, and the tag still there

        await press(pilot, "ctrl+r")
        assert dest.read_bytes() == b"pdf" and not src.exists()
        assert app.current["files"] == [str(dest)]


async def test_a_file_that_cannot_move_back_on_undo_keeps_its_entry_where_it_is(app, papis_lib):
    """Undoing `f r` wrote `files` back, then moved each file: one that could
    not move stayed in the PDF root while its entry named the old path — here
    another file, downloaded since under that name."""
    import yaml
    from cover_helpers import attach, info

    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    folder = papis_lib / "lib" / "doc0"
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "r")
        placed = Path(app.current["files"][0])
        assert placed.parent == papis_lib / "pdfs"
        (folder / "a.pdf").write_bytes(b"a new download under the old name")

        await press(pilot, "u")
        assert placed.read_bytes() == b"a.pdf"  # kept, and
        assert yaml.safe_load(info(papis_lib).read_text())["files"] == [str(placed), "b.pdf"]  # named where it is
        assert (folder / "a.pdf").read_bytes() == b"a new download under the old name"
        assert (folder / "b.pdf").read_bytes() == b"b.pdf"  # the other one moved back


async def test_relocating_a_batch_asks_first_counting_the_marks_the_narrow_hides(app, papis_lib):
    from cover_helpers import attach, info

    from ptui import ui

    attach(papis_lib, "doc0", "a.pdf")
    attach(papis_lib, "doc1", "b.pdf")
    before = [info(papis_lib, folder).read_text() for folder in ("doc0", "doc1")]
    files = [papis_lib / "lib" / "doc0" / "a.pdf", papis_lib / "lib" / "doc1" / "b.pdf"]
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        app.apply_narrow("attention")
        await settle(pilot)
        assert len(app.rows) == 1

        await press(pilot, "f", "r")
        assert isinstance(app.screen, ui.SelectList)
        assert "2 documents" in app.screen.title_text
        await press(pilot, "escape")  # cancelled: nothing moved, nothing recorded
        assert all(path.exists() for path in files)
        assert not app.history.done

        await press(pilot, "f", "r", "enter")
        assert not any(path.exists() for path in files)
        assert len(list((papis_lib / "pdfs").iterdir())) == 2

        await press(pilot, "u")  # one step for both
        assert all(path.exists() for path in files)
        assert not list((papis_lib / "pdfs").iterdir())
        assert [info(papis_lib, folder).read_text() for folder in ("doc0", "doc1")] == before


async def test_normalize_and_backfill_ask_first_on_a_batch_counting_what_the_narrow_hides(app, papis_lib):
    """Both had their own copy of the batch confirm."""
    from cover_helpers import info

    from ptui import ui

    for folder in ("doc0", "doc1"):
        path = info(papis_lib, folder)
        (path.parent / "a.djvu").write_bytes(b"x")  # not a PDF: the fixture's rules keep the style
        lines = path.read_text().splitlines(True)
        path.write_text("".join(line for line in lines if "time-added" not in line) + "files:\n- ./a.djvu\n")
    before = [info(papis_lib, folder).read_text() for folder in ("doc0", "doc1")]
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        app.apply_narrow("attention")
        await settle(pilot)
        assert len(app.rows) == 1

        for verb in (actions.files_normalize, actions.lib_backfill_dates):
            verb(app)
            assert isinstance(app.screen, ui.SelectList)
            assert "2 documents" in app.screen.title_text
            await press(pilot, "escape")  # cancelled: nothing written, nothing recorded
        assert [info(papis_lib, folder).read_text() for folder in ("doc0", "doc1")] == before
        assert not app.history.done


async def test_the_relocate_undo_step_counts_the_documents_whose_files_changed(app, papis_lib):
    """It counted every target, the ones with nothing to move too."""
    _attach(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}  # doc1 has no file
        await press(pilot, "f", "r", "enter")
        assert app.history.done[-1].label == "relocate the files of 1 document(s)"


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


async def test_bibtex_export_writes_papis_own_entry(app, tmp_path):
    out = tmp_path / "out.bib"
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.export_bibtex(app, str(out))
    assert "Vaswani2017" in out.read_text()


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


async def test_open_pick_opens_a_lone_file_without_asking(app, papis_lib, monkeypatch):
    import papis.utils

    from ptui import ui

    _attach(papis_lib)
    opened = []
    monkeypatch.setattr(papis.utils, "open_file", lambda *a, **k: opened.append(a))
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "o")
        assert not isinstance(app.screen, ui.SelectList)
    assert [Path(a[0]).name for a in opened] == ["downloaded paper.pdf"]


def test_parse_args_maps_a_typed_line_onto_the_signature():
    import pytest

    from ptui import commands

    assert commands.parse_args("sort.by", "year true") == {"key": "year", "reverse": True}
    assert commands.parse_args("sort.by", "year false") == {"key": "year", "reverse": False}
    assert commands.parse_args("nav.down", "5") == {"count": 5}
    assert commands.parse_args("pane.resize", "0.05") == {"delta": 0.05}
    assert commands.parse_args("export.bibtex", '"/tmp/two words.bib"') == {"target": "/tmp/two words.bib"}
    assert commands.parse_args("sort.by", "") == {}  # defaults stand
    assert commands.signature("sort.by") == "<key: str> [reverse: bool]"

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

        await press(pilot, "c", "T", *"test, cv", "enter")
        await settle(pilot)
        assert app.current["tags"] == ["ml"]

        await press(pilot, "c", "T", *"ml", "enter")  # the last one goes with the key
        await settle(pilot)
        assert "tags" not in app.current

    assert "papis_id: id0" in (papis_lib / "lib" / "doc0" / "info.yaml").read_text()


async def test_a_multi_word_tag_is_one_tag(app):
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "c", "t", *"machine learning, cv", "enter")
        await settle(pilot)
        assert app.current["tags"] == ["test", "machine learning", "cv"]

        await press(pilot, "c", "f", *"tags test, machine learning", "enter")
        await settle(pilot)
        assert app.current["tags"] == ["test", "machine learning"]

        await press(pilot, "E")  # the form shows the list joined with ", " and splits it back
        app.screen.query_one("#add-tags").value += ", deep nets"
        await press(pilot, "enter")
        assert app.current["tags"] == ["test", "machine learning", "deep nets"]

        await press(pilot, "c", "T", *"machine learning", "enter")
        await settle(pilot)
        assert app.current["tags"] == ["test", "deep nets"]


async def test_untag_and_its_undo_keep_a_tag_a_script_added_since_loading(app, papis_lib):
    """The new list is worked out from the file at write time, and undo puts back
    what the write replaced — not the list ptui loaded before the script ran."""
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    async with app.run_test() as pilot:
        await settle(pilot)
        info.write_text(info.read_text().replace("tags: [test]", "tags: [test, from-script]"))
        await press(pilot, "c", "T", *"test", "enter")
        await settle(pilot)
        assert app.current["tags"] == ["from-script"]
        await press(pilot, "u")
        assert app.current["tags"] == ["test", "from-script"]
        await press(pilot, "ctrl+r")
        assert app.current["tags"] == ["from-script"]


async def test_set_keeps_an_undeclared_float_a_float(app, papis_lib):
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    info.write_text(info.read_text() + "ratio: 1.5\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_set(app, "ratio", "2.5")
    assert "ratio: 2.5\n" in info.read_text()


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


@pytest.mark.parametrize(("mode", "key"), [("editor", "e"), ("structured", "E")])
async def test_edit_reports_yaml_it_can_no_longer_parse(papis_lib, monkeypatch, mode, key):
    """It said "press E again" whatever opened the editor, and with the shipped
    `edit.mode = "editor"` `E` is the form."""
    import contextlib

    import papis.commands.edit
    from conftest import build_app

    info = papis_lib / "lib" / "doc0" / "info.yaml"
    monkeypatch.setattr(papis.commands.edit, "run", lambda doc, **kw: info.write_text("title: [unclosed\n"))
    app = build_app(papis_lib, f'[edit]\nmode = "{mode}"\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        monkeypatch.setattr(app, "log_line", logged.append)
        # $EDITOR takes the terminal for real, which a headless pilot has not got
        monkeypatch.setattr(app, "suspend", contextlib.nullcontext)
        await press(pilot, key)  # $EDITOR on the whole file
        await settle(pilot)
        assert any("invalid YAML" in message for message in logged)
        assert any(f"press {key} again" in message for message in logged)
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
        await settle(pilot)
        assert not isinstance(app.screen, ui.SelectList)  # one document: no question, as for any verb

        stamped = {d["ref"]: d[library.TIME_ADDED] for d in app.docs}
        assert stamped["Vaswani2017"] == library.stamp(mtime)
        assert stamped["He2016"] == "2023-01-01-00:00:00"  # untouched
        assert app.docs[0]["ref"] == "Vaswani2017"  # and the list re-sorted


async def test_attach_from_inbox_is_papis_addto(app, papis_lib):
    import yaml

    inbox = papis_lib / "inbox"
    inbox.mkdir()
    (inbox / "supplement.pdf").write_bytes(b"supp")
    app.config.data["files"]["inbox"] = str(inbox)
    info = papis_lib / "lib" / "doc0" / "info.yaml"

    async with app.run_test() as pilot:
        await settle(pilot)
        info.write_text(info.read_text() + "note: edited since loading\n")
        await press(pilot, "f", "a")  # on doc0: "a file on disk…", then the inbox
        await press(pilot, "down", "enter")
        actions.files_attach(app, str(inbox / "supplement.pdf"))  # twice is a no-op

    data = yaml.safe_load(info.read_text())
    assert data["files"] == ["supplement.pdf"]
    assert data["note"] == "edited since loading"  # addto wrote a reloaded copy
    assert (info.parent / "supplement.pdf").read_bytes() == b"supp"
    assert not (papis_lib / "pdfs").exists()  # the fixture's move rule did not run
    assert (inbox / "supplement.pdf").exists()  # copied, never moved


async def test_attach_a_url_keeps_existing_files_and_drops_the_download(app, papis_lib, monkeypatch):
    import papis.paths
    import yaml

    _attach(papis_lib, "paper.pdf")
    download = papis_lib / "code.zip"

    def fake(uris):  # addto calls it again on the local copy, which passes through
        download.write_bytes(b"zip")
        return [str(download) if papis.paths.is_remote_file(u) else u for u in uris]

    monkeypatch.setattr(papis.paths, "download_remote_files", fake)
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.files_attach(app, "https://example.org/code.zip")
        await pilot.pause(0.3)

    folder = papis_lib / "lib" / "doc0"
    assert yaml.safe_load((folder / "info.yaml").read_text())["files"] == ["paper.pdf", "code.zip"]
    assert (folder / "code.zip").read_bytes() == b"zip"
    assert not download.exists()


async def test_mark_query_marks_without_changing_the_view_and_shift_unmarks(app):
    from conftest import press, settle

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "m", "q", *"year:>2010", "enter")
        assert app.marks == {"id0", "id1"}
        assert len(app.rows) == 3  # the view is untouched
        assert app.narrow_query == ""
        await press(pilot, "m", "Q", *"he", "enter")  # `He` wrote Deep Residual Learning
        assert app.marks == {"id0"}


async def test_mark_query_inside_a_narrow_marks_only_what_is_shown(app):
    """A document the narrow hides must not be marked: a verb on the marks would
    then act on something the user cannot see."""
    from conftest import press, settle

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "/", *"year:2000..2016", "enter")  # id1 (2016), id2 (2003)
        await press(pilot, "m", "q", *"year:>2010", "enter")  # id0 (2017) matches too, but is hidden
        assert app.marks == {"id1"}
        assert app.narrow_query == "year:2000..2016"  # the view is untouched


async def test_unmark_all_filtered_takes_back_only_what_is_shown(app):
    from conftest import press, settle

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "m", "a")
        assert len(app.marks) == 3
        await press(pilot, "/", *"neural", "enter")
        await press(pilot, "m", "A")
        assert app.marks == {"id0", "id1"}  # the hidden marks survive


async def test_visual_marks_the_range_as_the_cursor_moves_and_shift_unmarks(app):
    from ptui import library

    async with app.run_test() as pilot:
        await settle(pilot)
        ids = [library.doc_id(d) for d in app.rows]
        await press(pilot, "j", "v", "j")
        assert app.marks == set(ids[1:3])
        await press(pilot, "k", "k")  # back past the anchor: the range follows
        assert app.marks == set(ids[0:2])
        await press(pilot, "escape")  # ends the range, keeps the marks
        assert app.visual is None
        assert app.marks == set(ids[0:2])
        await press(pilot, "j", "j", "space", "g", "g")  # one more mark, outside any range
        await press(pilot, "V", "j")
        assert app.marks == {ids[2]}  # unmarked the range, the rest survived
        await press(pilot, "V")
        assert app.visual is None
        await press(pilot, "v", "m", "c", "k")  # another mark command ends the range
        assert app.marks == set()


async def test_a_jump_with_g_r_stretches_a_visual_range_to_its_target(app):
    """Pinned as it works today: `g r` is a move like `j`, so `v` then `g r`
    marks everything from the anchor to the document it lands on, both ways.
    (When `g r` has to drop a narrow to get there, the range ends instead —
    whether it should stretch then is still open.)"""
    from ptui import library

    async with app.run_test() as pilot:
        await settle(pilot)
        ids = [library.doc_id(d) for d in app.rows]
        await press(pilot, "v", "g", "r", *"Bengio2003", "enter")
        assert app.visual is not None
        assert app.current["ref"] == "Bengio2003"
        assert app.marks == set(ids)
        await press(pilot, "g", "r", *"He2016", "enter")  # back towards the anchor: the range shrinks
        assert app.marks == set(ids[:2])
        await press(pilot, "V")  # ends it
        await press(pilot, "V", "g", "r", *"Bengio2003", "enter")
        assert app.marks == {ids[0]}  # the unmarking half stretches the same way


async def test_query_prompts_share_a_history_that_up_and_down_walk(app, papis_lib):
    from textual.widgets import Input

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "s", *"year:>2010", "enter")
        await settle(pilot)
        await press(pilot, "/", *"neural", "enter", "escape")
        await press(pilot, "s", *"year:>2010", "enter")  # again: moves to the end, once
        await settle(pilot)
        assert (papis_lib / "history").read_text().splitlines() == ["neural", "year:>2010"]

        prompt = app.query_one(Input)
        await press(pilot, "/", "up")
        assert prompt.value == "year:>2010"
        assert len(app.rows) == 2  # recalled into the narrow prompt, so it narrows
        await press(pilot, "up", "up")  # stops at the oldest
        assert prompt.value == "neural"
        await press(pilot, "down", "down")  # past the newest: what was being typed
        assert prompt.value == ""


async def test_up_after_typing_walks_only_the_history_that_starts_with_it(app, papis_lib):
    from textual.widgets import Input

    (papis_lib / "history").write_text("neural\nyear:>2010\nne\nnet\nyear:<2000\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        prompt = app.query_one(Input)
        await press(pilot, "s", *"ne", "up")
        assert prompt.value == "net"  # `year:<2000` is newer but does not start with `ne`
        await press(pilot, "up")
        assert prompt.value == "neural"  # `ne` itself is skipped: recalling it changes nothing
        await press(pilot, "up")  # stops at the oldest match
        assert prompt.value == "neural"
        await press(pilot, "down", "down")
        assert prompt.value == "ne"


def test_refetchers_come_from_the_document_itself_without_the_network():
    names = lambda data: [imp.name for imp in fetch.refetchers(data)]  # noqa: E731
    assert names({"title": "T", "doi": "10.1/x"}) == ["doi", "crossref"]
    assert names({"title": "T", "eprint": "1706.03762", "eprinttype": "arXiv"}) == ["arxiv", "crossref"]
    assert names({"title": "T", "eprint": "rJ4km2R5t7", "eprinttype": "openreview"}) == ["crossref"]
    assert names({}) == []


def test_refetch_by_id_never_downloads_and_drops_the_keys_that_are_the_librarys_own(monkeypatch):
    def fetch_importers(importers, download_files):  # the network, and nothing else
        assert not download_files
        for importer in importers:
            importer.ctx.data = {"title": "T", "ref": "Theirs2020", "citations": [{"doi": "10.1/y"}], "files": ["x"]}
        return list(importers)

    monkeypatch.setattr("papis.importer.fetch_importers", fetch_importers)
    (by_doi,) = fetch.refetchers({"doi": "10.1/x"})
    assert fetch.refetch(by_doi) == [{"title": "T"}]


def _crossref(monkeypatch, hits):
    """papis's Crossref search, without the network; the calls are recorded."""
    asked = []
    monkeypatch.setattr("papis.crossref.get_data", lambda **kwargs: asked.append(kwargs) or [dict(h) for h in hits])
    return asked


HITS = [
    {"title": "Attention Is Not All You Need", "year": 2021, "author_list": [{"family": "Dong", "given": "Y"}]},
    {
        "title": "Attention Is All You Need",
        "year": 2017,
        "author_list": [{"family": "Vaswani", "given": "X"}],
        "journal": "NeurIPS",
        "doi": "10.5555/3295222.3295349",
    },
]


async def test_refresh_from_crossref_lists_the_hits_and_merges_the_one_picked(app, papis_lib, monkeypatch):
    """papis's importer kept the first hit ("Picking the first one!"): here, another paper."""
    from ptui import ui

    asked = _crossref(monkeypatch, HITS)
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "u")  # title only: Crossref is the one source
        await settle(pilot)
        assert asked == [{"query": "Attention Is All You Need", "max_results": fetch.CROSSREF_HITS}]
        assert isinstance(app.screen, ui.SelectList)
        assert [item.label for item in app.screen.items] == [*(hit["title"] for hit in HITS), "none of these"]
        assert [item.hint for item in app.screen.items[:2]] == ["2021 · Dong", "2017 · Vaswani · NeurIPS"]
        await press(pilot, "down", "enter")
        await settle(pilot)
    assert "doi: 10.5555/3295222.3295349" in info.read_text()  # a gap of the paper picked


async def test_none_of_the_crossref_hits_writes_nothing(app, papis_lib, monkeypatch):
    _crossref(monkeypatch, HITS)
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    before = info.read_text()
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "u")
        await settle(pilot)
        await press(pilot, "down", "down", "enter")  # none of these
        await settle(pilot)
    assert info.read_text() == before


async def test_adding_from_crossref_lists_the_hits_and_fills_the_form_with_the_one_picked(app, monkeypatch):
    """papis's Crossref importer matches no typed text at all: this source never worked."""
    from textual.widgets import Input

    from ptui import ui

    asked = _crossref(monkeypatch, HITS)
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.cmdline_run(app, "doc.add", "crossref 'attention is all'")
        await app.workers.wait_for_complete()
        await settle(pilot)
        assert asked == [{"query": "attention is all", "max_results": fetch.CROSSREF_HITS}]
        assert isinstance(app.screen, ui.SelectList)
        assert app.screen.items[-1].label == "none of these"
        await press(pilot, "down", "enter")
        assert isinstance(app.screen, ui.AddForm)
        assert app.screen.query_one("#add-title", Input).value == "Attention Is All You Need"


async def test_refresh_fills_gaps_asks_each_clash_and_undoes_in_one_step(app, papis_lib, monkeypatch):
    from ptui import ui

    fetched = {
        "title": "Attention is All you Need",  # the publisher's capitalisation: a question
        "year": 2017,  # stored as an int already: no question
        "author_list": [{"family": "Vaswani", "given": "X"}],
        "doi": "10.5555/3295222.3295349",  # a gap: filled without asking
        "citations": [{"doi": "10.1/y"}],  # never into info.yaml
    }

    _crossref(monkeypatch, [fetched])  # one hit: "is this the paper?"
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "u")  # title only: Crossref is the one source
        await settle(pilot)
        assert "Attention is All you Need (2017 · Vaswani) — is this the paper?" in app.screen.title_text
        await press(pilot, "enter")  # yes
        assert isinstance(app.screen, ui.SelectList)
        assert [item.label for item in app.screen.items][:2] == [
            "Attention Is All You Need",
            "Attention is All you Need",
        ]
        await press(pilot, "enter")  # keep ours
        await settle(pilot)
        text = info.read_text()
        assert "doi: 10.5555/3295222.3295349" in text
        assert "title: Attention Is All You Need" in text
        assert "citations" not in text
        await press(pilot, "u")
        await settle(pilot)
        assert "doi" not in info.read_text()


async def test_refresh_from_crossref_writes_nothing_when_it_found_another_paper(app, papis_lib, monkeypatch):
    """A title search can find the wrong paper, and its gaps were filled silently."""

    _crossref(monkeypatch, [{"title": "Attention Is Not All You Need", "year": 2021, "doi": "10.1/other"}])
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    before = info.read_text()
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "u")
        await settle(pilot)
        assert "Attention Is Not All You Need (2021) — is this the paper?" in app.screen.title_text
        await press(pilot, "down", "enter")  # no
        await settle(pilot)
    assert info.read_text() == before


async def test_export_as_offers_every_papis_exporter_and_yanks_the_targets(app, monkeypatch):
    from ptui import ui

    yanked = []
    monkeypatch.setattr(clip, "copy", lambda _app, text: yanked.append(text) or "test")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "y", "e")
        assert isinstance(app.screen, ui.SelectList)
        assert {"bibtex", "json", "yaml"} <= {item.label for item in app.screen.items}
        await press(pilot, *"json", "enter")
        await settle(pilot)
    assert '"title": "Attention Is All You Need"' in yanked[0]


@pytest.mark.parametrize("to_file", [True, False])
async def test_an_export_papis_failed_writes_and_copies_nothing(app, tmp_path, monkeypatch, to_file):
    """papis's export returns "" on failure (`csl` without citeproc-py), and that
    "" was written over the target, or copied with a success message."""
    import papis.commands.export

    target = tmp_path / "refs.txt"
    target.write_text("what was there\n")
    yanked = []
    monkeypatch.setattr(clip, "copy", lambda _app, text: yanked.append(text) or "test")
    monkeypatch.setattr(papis.commands.export, "run", lambda _docs, _fmt: "")
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        monkeypatch.setattr(app, "log_line", logged.append)
        actions.export_as(app, "csl", str(target) if to_file else None)
    assert target.read_text() == "what was there\n"
    assert yanked == []
    assert any("export as csl failed" in line for line in logged)


async def test_config_set_applies_for_the_session_and_checks_the_value(app):
    from ptui import actions

    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        actions.cmdline_run(app, "config.set", "list.row_height 2")
        await pilot.pause(0.2)
        assert app.config.get("list.row_height") == 2
        assert app.query_one("DataTable").get_row_at(0)  # redrawn
        assert app.query_one("DataTable").rows[app.query_one("DataTable").coordinate_to_cell_key((0, 0))[0]].height == 2

        actions.cmdline_run(app, "config.set", "ui.theme nord")  # bare text, no quotes needed
        assert app.theme == "nord"

        actions.cmdline_run(app, "config.set", "ui.layout diagonal")
        assert app.config.get("ui.layout") == "auto"  # repaired, as config.toml would be

        actions.cmdline_run(app, "config.set", "ui.nonsense 1")
        assert app.config.get("ui.nonsense") is None  # a new key would be read by nothing


def test_config_set_refuses_a_key_read_only_at_startup(app, monkeypatch):
    """Setting one used to report "until you quit" and change nothing at all."""
    from ptui import actions

    logged = []
    monkeypatch.setattr(app, "log_line", logged.append)
    for key in (
        "log.level",
        "log.file",
        "log.max_entries",
        "ui.mouse",
        "undo.stack_size",
        "general.startup_query",
        "doctor.scan_on_startup",
    ):
        before = app.config.get(key)
        actions.config_set(app, key, "1")
        assert app.config.get(key) == before
        assert f"{key} takes effect on restart" in logged.pop()


async def test_goto_jumps_to_a_pasted_citekey_and_drops_a_hiding_narrow(app):
    from conftest import press, settle

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "/", *"attention", "enter")
        assert len(app.rows) == 1
        await press(pilot, "g", "r", *r"\citep[p.~3]{bengio2003,He2016}", "enter")
        assert app.current["ref"] == "Bengio2003"  # the first key, case-insensitively
        assert app.narrow_query == ""
        await press(pilot, "g", "r", *"Nobody1999", "enter")
        assert app.current["ref"] == "Bengio2003"  # unknown: stays put, says so


async def test_goto_without_an_exact_ref_warns_and_takes_the_one_that_contains_it(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        real = app.log_line
        app.log_line = lambda message: (logged.append(message), real(message))
        actions.nav_goto(app, "bengio")  # a substring of exactly one ref
        assert app.current["ref"] == "Bengio2003"
        assert "no ref bengio" in logged[-1]
        actions.nav_goto(app, "20")  # in all three: nowhere to go
        assert app.current["ref"] == "Bengio2003"
        assert "3 refs contain it" in logged[-1]
        actions.nav_goto(app, "vaswani2017")  # equal but for case
        assert app.current["ref"] == "Vaswani2017"
        assert "no ref vaswani2017" in logged[-1]


async def test_goto_takes_the_one_near_match_the_narrowed_list_shows(app):
    """All three refs contain `20`; with one of them on screen, that one was meant."""
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        real = app.log_line
        app.log_line = lambda message: (logged.append(message), real(message))
        app.apply_narrow("-attention")  # Bengio2003 and He2016
        await settle(pilot)
        actions.nav_goto(app, "20")  # still two shown: listed, as before
        assert "3 refs contain it" in logged[-1]
        app.apply_narrow("bengio")
        await settle(pilot)
        actions.nav_goto(app, "20")
        assert app.current["ref"] == "Bengio2003"
        assert "went to Bengio2003" in logged[-1]
        assert app.narrow_query == "bengio"  # it is shown: nothing to drop


async def test_a_saved_search_comes_back_with_its_sort_and_can_be_forgotten(app, papis_lib):
    import json

    from conftest import press, settle

    from ptui import ui

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "s", *"year:2016", "enter")
        await press(pilot, "ctrl+s")
        await press(pilot, "\\", "s", *"resnet", "enter")
        saved = json.loads((papis_lib / "saved.json").read_text())
        assert saved["resnet"] == {"scope": "year:2016", "sort": "time-added", "reverse": False}

        await press(pilot, "s", "ctrl+u", "enter")  # back to everything, default sort
        await press(pilot, "ctrl+s")
        assert len(app.docs) == 3
        await press(pilot, "g", "s")
        assert isinstance(app.screen, ui.SelectList)
        assert {item.value for item in app.screen.items} >= {"resnet", "to-read"}  # config's too
        await press(pilot, *"resnet", "enter")
        assert app.scope_query == "year:2016"
        assert app.sort_reverse is False

        await press(pilot, "g", "s", *"resnet", "shift+enter")
        assert "resnet" not in json.loads((papis_lib / "saved.json").read_text())


async def test_g_m_shows_only_the_marked(app):
    from conftest import press, settle

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space", "g", "m")
        assert len(app.rows) == 1


async def test_the_structured_editor_writes_typed_values_and_undoes_in_one_step(app, papis_lib):
    from conftest import press, settle

    from ptui import ui

    info = papis_lib / "lib" / "doc0" / "info.yaml"
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "E")  # the form: the other editor by default
        assert isinstance(app.screen, ui.EditForm)
        form = app.screen
        assert form.query_one("#add-author_list").value == "Vaswani, X"
        form.query_one("#add-year").value = "2018"
        form.query_one("#add-tags").value = "nlp, attention"
        form.query_one("#add-author_list").value = "Vaswani, Ashish and Shazeer, Noam"
        form.query_one("#add-ref").value = ""  # emptied: removed
        await press(pilot, "enter")
        doc = app.current
        assert doc["year"] == 2018  # an int, as papis declares it
        assert doc["tags"] == ["nlp", "attention"]
        assert [a["family"] for a in doc["author_list"]] == ["Vaswani", "Shazeer"]
        assert "ref" not in doc
        assert "title: Attention Is All You Need" in info.read_text()  # untouched

        await press(pilot, "u")
        assert app.current["year"] == 2017
        assert app.current["ref"] == "Vaswani2017"
        assert app.current["tags"] == ["test"]


async def test_the_structured_editor_refuses_a_value_of_the_wrong_type(app, papis_lib):
    from conftest import press, settle

    before = (papis_lib / "lib" / "doc0" / "info.yaml").read_text()
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "E")  # the form: the other editor by default
        app.screen.query_one("#add-year").value = "soon"
        await press(pilot, "enter")
        assert (papis_lib / "lib" / "doc0" / "info.yaml").read_text() == before


async def test_edit_mode_structured_swaps_e_and_shift_e(papis_lib, monkeypatch):
    from conftest import build_app, press, settle

    from ptui import actions, ui

    raw = []
    monkeypatch.setattr(actions, "doc_edit_raw", lambda app: raw.append(True))
    app = build_app(papis_lib, '[edit]\nmode = "structured"\n')
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "e")
        assert isinstance(app.screen, ui.EditForm)
        await press(pilot, "escape")
        await press(pilot, "E")
        assert raw == [True]


async def test_editing_the_author_line_keeps_what_each_matching_author_had(app, papis_lib):
    """Editing the author line rebuilt `author_list` from the names alone, and
    every author lost their affiliation and ORCID."""
    from conftest import press, settle

    info = papis_lib / "lib" / "doc0" / "info.yaml"
    info.write_text(info.read_text().replace("  given: X\n", "  given: X\n  orcid: 0000-0001\n"))
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "E")
        app.screen.query_one("#add-author_list").value = "Vaswani, X and Shazeer, Noam"
        await press(pilot, "enter")
        authors = app.current["author_list"]
        assert authors[0] == {"family": "Vaswani", "given": "X", "orcid": "0000-0001"}
        assert authors[1]["family"] == "Shazeer" and "orcid" not in authors[1]
        assert "orcid: 0000-0001" in info.read_text()


async def test_a_document_with_no_citekey_is_named_in_the_log_and_nothing_is_yanked(app, papis_lib, monkeypatch):
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    info.write_text(info.read_text().replace("ref: Vaswani2017\n", ""))
    yanked = []
    monkeypatch.setattr(clip, "copy", lambda _app, text: yanked.append(text) or "test")
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        monkeypatch.setattr(app, "log_line", logged.append)
        actions.export_citekey(app)
    assert yanked == []
    assert logged == [
        "[yellow]no citekey for Attention Is All You Need; skipped[/]",
        "[yellow]nothing to yank for 0 citekey(s)[/]",
    ]


async def test_r_finds_an_info_yaml_edited_on_disk_after_startup(app, papis_lib):
    """papis's own cache never sees an edit made outside papis: vim, a sync, `git pull`."""
    from conftest import press, settle

    info = papis_lib / "lib" / "doc0" / "info.yaml"
    async with app.run_test() as pilot:
        await settle(pilot)
        info.write_text(info.read_text().replace("Attention Is All You Need", "Edited Elsewhere"))
        await press(pilot, "r")
        await settle(pilot)
        assert "Edited Elsewhere" in [doc["title"] for doc in app.docs]
