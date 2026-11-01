"""Edges of delete and undo: what the dialog offers for unusual `files`
entries, a git delete that cannot happen, and git undo/redo with a file under
`pdf_root` riding along."""

import subprocess
from pathlib import Path

import pytest
from conftest import build_app, press, settle

from ptui import undo


def _add_entry(papis_lib, doc, entry):
    info = papis_lib / "lib" / doc / "info.yaml"
    text = info.read_text()
    info.write_text(text + (f"- {entry}\n" if "files:" in text else f"files:\n- {entry}\n"))


def _pdf(papis_lib, name="paper.pdf"):
    root = papis_lib / "pdfs"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(b"pdf")
    return path


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_restore_never_clobbers_a_path_that_came_back_and_skips_what_is_gone(tmp_path):
    trashed, back = tmp_path / "trash" / "a", tmp_path / "lib" / "a"
    trashed.mkdir(parents=True)
    (trashed / "info.yaml").write_text("old")
    back.mkdir(parents=True)
    (back / "info.yaml").write_text("new")  # something took the name back meanwhile

    gone = (tmp_path / "lib" / "b", tmp_path / "trash" / "b")  # emptied from the trash
    deep = (tmp_path / "new" / "parents" / "c", tmp_path / "trash" / "c")
    deep[1].mkdir()

    assert undo.restore([(back, trashed), gone, deep]) == [deep[0]]
    assert (back / "info.yaml").read_text() == "new"
    assert (trashed / "info.yaml").read_text() == "old"  # still recoverable
    assert deep[0].is_dir()


def test_a_history_of_one_keeps_only_the_last_step():
    calls = []
    history = undo.History(size=1)
    for name in "ab":
        history.push(undo.Step(name, lambda name=name: calls.append(name), lambda: None))
    assert history.undo().label == "b"
    assert history.undo() is None
    assert calls == ["b"]


@pytest.mark.xfail(
    strict=True,
    reason="BUG: _doomed_files tests `folder in path.parents` on the unresolved path, so "
    "`../../pdfs/x.pdf` counts as inside the folder and is never offered — it is left orphaned",
)
async def test_a_relative_entry_that_climbs_out_of_the_folder_is_offered(papis_lib):
    _pdf(papis_lib)
    _add_entry(papis_lib, "doc0", "../../pdfs/paper.pdf")
    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        assert [Path(choice.label).name for choice in app.screen.files] == ["paper.pdf"]
        await press(pilot, "escape")


async def test_a_shared_file_spelled_differently_is_never_checked(papis_lib):
    shared = _pdf(papis_lib, "shared.pdf")
    _add_entry(papis_lib, "doc0", str(shared))
    _add_entry(papis_lib, "doc1", "../../pdfs/shared.pdf")
    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        assert not app.screen.files[0].checked
        await press(pilot, "escape")


@pytest.mark.xfail(
    strict=True,
    reason="BUG: _delete_run trashes the checked files before _delete_with_git finds there is no "
    "repository; the delete fails, no undo step is recorded and the files stay in the trash",
)
async def test_a_git_delete_that_cannot_run_moves_no_file(papis_lib):
    pdf = _pdf(papis_lib)
    _add_entry(papis_lib, "doc0", str(pdf))
    app = build_app(papis_lib, extra='strategy = "git"\n')  # the fixture library is no repository
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d", "enter")
        await settle(pilot)
        assert (papis_lib / "lib" / "doc0").is_dir()  # the delete itself did not happen
        assert pdf.exists() or app.history.done


async def test_git_undo_brings_back_a_pdf_root_file_and_redo_takes_it_again(git_lib):
    repo = git_lib / "lib"
    pdf = _pdf(git_lib)
    _add_entry(git_lib, "doc0", str(pdf))
    _git(repo, "commit", "-qam", "attach")
    app = build_app(git_lib, extra='strategy = "git"\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d", "enter")  # pdf_root is checked by default
        await settle(pilot)
        assert not pdf.exists() and not (repo / "doc0").exists()

        await press(pilot, "u")
        await settle(pilot)
        assert pdf.read_bytes() == b"pdf"  # outside git, so it came back from the trash
        assert (repo / "doc0" / "info.yaml").exists()

        await press(pilot, "ctrl+r")
        await settle(pilot)
        assert not pdf.exists() and not (repo / "doc0").exists()
        assert _git(repo, "log", "-1", "--format=%s") == "ptui: delete 1 document(s)"

        await press(pilot, "u")  # the redo is what this undo reverses
        await settle(pilot)
        assert pdf.exists() and (repo / "doc0" / "info.yaml").exists()
        assert _git(repo, "status", "--porcelain") == ""
