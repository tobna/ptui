"""Edges of delete and undo.

What the dialog offers for unusual `files` entries, a git delete that cannot happen, and git
undo/redo with a file under `pdf_root` riding along.
"""

import subprocess
from pathlib import Path

from conftest import build_app, press, settle

from ptui import undo


def _add_entry(papis_lib, doc, entry):
    """Append an entry to a document's `files`.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture.
        doc: The document's folder name.
        entry: The entry to add.
    """
    info = papis_lib / "lib" / doc / "info.yaml"
    text = info.read_text()
    info.write_text(text + (f"- {entry}\n" if "files:" in text else f"files:\n- {entry}\n"))


def _pdf(papis_lib, name="paper.pdf"):
    """Create a file under the PDF root.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture.
        name: The file's name.

    Returns:
        The file's path.
    """
    root = papis_lib / "pdfs"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(b"pdf")
    return path


def _git(repo, *args):
    """Run git in a repository.

    Args:
        repo: The repository.
        *args: git's arguments.

    Returns:
        git's standard output, stripped.
    """
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_restore_never_clobbers_a_path_that_came_back_and_skips_what_is_gone(tmp_path):
    """Restore never clobbers a path that came back and skips what is gone.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
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
    """A history of one keeps only the last step."""
    calls = []
    history = undo.History(size=1)
    for name in "ab":
        history.push(undo.Step(name, lambda name=name: calls.append(name), lambda: None))
    assert history.undo().label == "b"
    assert history.undo() is None
    assert calls == ["b"]


async def test_a_relative_entry_that_climbs_out_of_the_folder_is_offered(papis_lib):
    """A relative entry that climbs out of the folder is offered.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    _pdf(papis_lib)
    _add_entry(papis_lib, "doc0", "../../pdfs/paper.pdf")
    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        assert [Path(choice.label).name for choice in app.screen.files] == ["paper.pdf"]
        await press(pilot, "escape")


async def test_a_shared_file_spelled_differently_is_never_checked(papis_lib):
    """A shared file spelled differently is never checked.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    shared = _pdf(papis_lib, "shared.pdf")
    _add_entry(papis_lib, "doc0", str(shared))
    _add_entry(papis_lib, "doc1", "../../pdfs/shared.pdf")
    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        assert not app.screen.files[0].checked
        await press(pilot, "escape")


async def test_a_git_delete_that_cannot_run_moves_no_file(papis_lib):
    """A git delete that cannot run moves no file.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
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
    """Git undo brings back a pdf root file and redo takes it again.

    Args:
        git_lib: The temporary folder of the `git_lib` fixture, its `lib/` a repository.
    """
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
