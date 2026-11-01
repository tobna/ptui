"""The file verbs: detach, reorder, repoint, rename and attach, each on the
entry under the files pane's cursor or a given index — out-of-range indices,
the last entry, a write that fails after the file moved, failed downloads."""

import papis.config
import papis.paths
import pytest
import yaml
from conftest import press, settle
from cover_helpers import attach, info, logs

from ptui import actions, safewrite

SCHEME = "{doc[year]}-{doc[author_list][0][family]}"


def _files(papis_lib, folder: str = "doc0") -> list[str] | None:
    return yaml.safe_load(info(papis_lib, folder).read_text()).get("files")


def _fail_writes(monkeypatch) -> None:
    def broken(info):
        raise OSError("disk full")

    monkeypatch.setattr(safewrite, "write", broken)


async def test_detaching_the_last_file_removes_the_key_and_undo_brings_it_back(app, papis_lib):
    attach(papis_lib, "doc0", "only.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3", "x")
        assert _files(papis_lib) is None
        assert "files:" not in info(papis_lib).read_text()  # no empty list left behind
        assert (papis_lib / "lib" / "doc0" / "only.pdf").exists()
        await press(pilot, "u")
        assert _files(papis_lib) == ["only.pdf"]


@pytest.mark.parametrize("command", [actions.files_detach, actions.files_reorder, actions.files_rename])
async def test_an_index_past_the_end_is_reported_and_writes_nothing(app, papis_lib, monkeypatch, command):
    attach(papis_lib, "doc0", "a.pdf")
    before = info(papis_lib).read_text()
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        command(app, 3)
        assert any("no file 3; this document has 1" in line for line in logged)
        assert not app.history.done
    assert info(papis_lib).read_text() == before


@pytest.mark.parametrize("command", [actions.files_detach, actions.files_repoint, actions.files_rename])
async def test_a_document_with_no_files_says_so(app, monkeypatch, command):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        command(app)
        assert logged == ["[yellow]no files attached[/]"]


async def test_reordering_past_either_end_is_a_no_op(app, papis_lib):
    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    before = info(papis_lib).read_text()
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3", "K")  # the first entry up
        actions.files_reorder(app, 1, 5)  # the last one down
        assert not app.history.done
    assert info(papis_lib).read_text() == before


async def test_reordering_by_index_from_the_command_line_undoes(app, papis_lib):
    attach(papis_lib, "doc0", "a.pdf", "b.pdf", "c.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.cmdline_run(app, "files.reorder", "2 -2")
        assert _files(papis_lib) == ["c.pdf", "a.pdf", "b.pdf"]
        await press(pilot, "u")
        assert _files(papis_lib) == ["a.pdf", "b.pdf", "c.pdf"]


async def test_repointing_to_a_missing_path_changes_nothing(app, papis_lib, monkeypatch):
    attach(papis_lib, "doc0", "a.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.files_repoint(app, 0, str(papis_lib / "nowhere.pdf"))
        assert any("no such file" in line for line in logged)
    assert _files(papis_lib) == ["a.pdf"]


async def test_repointing_an_absolute_entry_keeps_it_absolute(app, papis_lib):
    folder = papis_lib / "lib" / "doc0"
    (folder / "new.pdf").write_bytes(b"new")
    info(papis_lib).write_text(info(papis_lib).read_text() + f"files:\n- {folder / 'old.pdf'}\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.prompt_result(app, "repoint:0", str(folder / "new.pdf"))  # what the prompt hands over
    assert _files(papis_lib) == [str(folder / "new.pdf")]


async def test_choosing_a_path_in_the_repoint_picker_asks_for_it(app, papis_lib):
    attach(papis_lib, "doc0", "a.pdf")
    other = papis_lib / "elsewhere.pdf"
    other.write_bytes(b"x")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "3", "p")
        assert [item.label for item in app.screen.items] == ["a path…"]  # no loose files
        await press(pilot, "enter")
        assert app.prompt_kind == "repoint:0"
        await press(pilot, *str(other), "enter")
    assert _files(papis_lib) == ["../../elsewhere.pdf"]  # the relative entry stays relative


async def test_renaming_twice_says_the_name_already_follows_the_scheme(app, papis_lib, monkeypatch):
    attach(papis_lib, "doc0", "paper.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        papis.config.set("add-file-name", SCHEME)
        actions.files_rename(app, 0)
        assert _files(papis_lib) == ["2017-vaswani.pdf"]
        logged = logs(app, monkeypatch)
        actions.files_rename(app, 0)
        assert any("already follows the scheme" in line for line in logged)
        assert len(app.history.done) == 1


async def test_renaming_a_missing_file_points_at_repoint(app, papis_lib, monkeypatch):
    info(papis_lib).write_text(info(papis_lib).read_text() + "files:\n- gone.pdf\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        papis.config.set("add-file-name", SCHEME)
        logged = logs(app, monkeypatch)
        actions.files_rename(app, 0)
        assert any("missing file" in line and "p repoints" in line for line in logged)
    assert _files(papis_lib) == ["gone.pdf"]


async def test_renaming_never_clobbers_a_file_already_at_the_name(app, papis_lib):
    attach(papis_lib, "doc0", "paper.pdf")
    folder = papis_lib / "lib" / "doc0"
    (folder / "2017-vaswani.pdf").write_bytes(b"someone else's")
    async with app.run_test() as pilot:
        await settle(pilot)
        papis.config.set("add-file-name", SCHEME)
        actions.files_rename(app, 0)
    assert (folder / "2017-vaswani.pdf").read_bytes() == b"someone else's"
    assert _files(papis_lib) == ["2017-vaswani-a.pdf"]
    assert (folder / "2017-vaswani-a.pdf").read_bytes() == b"paper.pdf"


async def test_a_rename_whose_write_fails_puts_the_file_back(app, papis_lib, monkeypatch):
    """SPEC's commit order: file first, info.yaml second, and a failed write
    takes the file back — or the entry would name a file that is gone."""
    attach(papis_lib, "doc0", "paper.pdf")
    folder = papis_lib / "lib" / "doc0"
    async with app.run_test() as pilot:
        await settle(pilot)
        papis.config.set("add-file-name", SCHEME)
        _fail_writes(monkeypatch)
        with pytest.raises(OSError, match="disk full"):
            actions.files_rename(app, 0)
        assert not app.history.done
    assert (folder / "paper.pdf").read_bytes() == b"paper.pdf"
    assert not (folder / "2017-vaswani.pdf").exists()
    assert _files(papis_lib) == ["paper.pdf"]


async def test_redoing_a_rename_renames_it_again(app, papis_lib):
    attach(papis_lib, "doc0", "paper.pdf")
    folder = papis_lib / "lib" / "doc0"
    async with app.run_test() as pilot:
        await settle(pilot)
        papis.config.set("add-file-name", SCHEME)
        actions.files_rename(app, 0)
        await press(pilot, "u", "ctrl+r")
    assert _files(papis_lib) == ["2017-vaswani.pdf"]
    assert sorted(p.name for p in folder.iterdir()) == ["2017-vaswani.pdf", "info.yaml"]


async def test_attaching_a_path_that_is_not_a_file_says_so(app, papis_lib, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.files_attach(app, str(papis_lib / "lib"))  # a directory
        assert any("no such file" in line for line in logged)
    assert _files(papis_lib) is None


async def test_the_attach_picker_without_an_inbox_still_offers_a_path(app, papis_lib):
    paper = papis_lib / "paper.pdf"
    paper.write_bytes(b"pdf")
    app.cfg.data["files"]["inbox"] = str(papis_lib / "no-inbox")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "a")
        assert [item.label for item in app.screen.items] == ["a file on disk or a URL…"]
        await press(pilot, "enter")
        assert app.prompt_kind == "attach"
        await press(pilot, *str(paper), "enter")
    assert _files(papis_lib) == ["paper.pdf"]


@pytest.mark.parametrize("download", [None, OSError("connection refused")])
async def test_a_failed_download_attaches_nothing_and_says_so(app, papis_lib, monkeypatch, download):
    def fake(uris):
        if isinstance(download, Exception):
            raise download
        return [download]

    monkeypatch.setattr(papis.paths, "download_remote_files", fake)
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.files_attach(app, "https://example.org/paper.pdf")
        await app.workers.wait_for_complete()
        await settle(pilot)
        assert any("download failed" in line for line in logged)
    assert _files(papis_lib) is None


async def test_an_attach_papis_refuses_is_reported_and_reloads_from_disk(app, papis_lib, monkeypatch):
    import papis.commands.addto

    paper = papis_lib / "paper.pdf"
    paper.write_bytes(b"pdf")

    def refuse(doc, paths, git=False):
        doc["files"] = ["half-written.pdf"]  # what a failing addto may leave in memory
        raise RuntimeError("addto broke")

    monkeypatch.setattr(papis.commands.addto, "run", refuse)
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.files_attach(app, str(paper))
        assert any("attach failed" in line for line in logged)
        assert "files" not in app.current  # what is on disk, not what addto left
    assert paper.exists()


async def test_an_attach_that_adds_nothing_points_at_the_papis_log(app, papis_lib, monkeypatch):
    import papis.commands.addto

    paper = papis_lib / "paper.pdf"
    paper.write_bytes(b"pdf")
    monkeypatch.setattr(papis.commands.addto, "run", lambda doc, paths, git=False: None)
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        actions.files_attach(app, str(paper))
        assert any("nothing attached" in line for line in logged)


async def test_a_mistyped_home_path_at_the_attach_prompt_is_reported_not_fatal(app, monkeypatch):
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        await press(pilot, "f", "a", "enter")  # "a file on disk or a URL…"
        await press(pilot, *"~nosuchuser-ptui/paper.pdf", "enter")
        assert app.is_running
        assert logged


async def test_a_stale_file_aborts_a_detach_and_records_no_step(app, papis_lib, monkeypatch):
    attach(papis_lib, "doc0", "a.pdf")

    def stale(info):
        raise safewrite.StaleError("changed on disk")

    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        monkeypatch.setattr(safewrite, "write", stale)
        actions.files_detach(app, 0)
        assert any("changed on disk; press r" in line for line in logged)
        assert not app.history.done
    assert _files(papis_lib) == ["a.pdf"]


@pytest.mark.parametrize("command", [actions.files_detach, actions.files_reorder])
async def test_a_files_key_written_as_one_string_is_not_split_into_letters(app, papis_lib, command):
    (papis_lib / "lib" / "doc0" / "paper.pdf").write_bytes(b"pdf")
    info(papis_lib).write_text(info(papis_lib).read_text() + "files: paper.pdf\n")
    async with app.run_test() as pilot:
        await settle(pilot)
        command(app, 0)
    assert _files(papis_lib) in (None, "paper.pdf", ["paper.pdf"])
