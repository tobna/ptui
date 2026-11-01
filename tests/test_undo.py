"""Delete and undo, over all three strategies.

Every test here runs against the throwaway library in `tmp_path` — documents,
`pdf_root`, trash and git repo included. Nothing reaches a real library.
"""

import subprocess
from pathlib import Path

import pytest
from conftest import build_app, press, settle

from ptui import undo


def _attach(papis_lib, doc="doc0", name="paper.pdf", *, outside=True):
    """Give a document a file, under `pdf_root` (outside its folder) or inside it."""
    folder = papis_lib / "lib" / doc
    if outside:
        root = papis_lib / "pdfs"
        root.mkdir(exist_ok=True)
        path = root / name
        entry = str(path)
    else:
        path = folder / name
        entry = name
    path.write_bytes(b"pdf")
    info = folder / "info.yaml"
    text = info.read_text()
    info.write_text(text + (f"- {entry}\n" if "files:" in text else f"files:\n- {entry}\n"))
    return path


# ── the history itself ──────────────────────────────────────────────────────


def test_history_is_bounded_and_redo_dies_on_a_new_branch():
    seen = []
    step = lambda n: undo.Step(n, lambda: seen.append(f"-{n}"), lambda: seen.append(f"+{n}"))  # noqa: E731

    history = undo.History(size=2)
    for name in ("a", "b", "c"):
        history.push(step(name))
    assert [s.label for s in history.done] == ["b", "c"]  # "a" fell off the end

    assert history.undo().label == "c"
    assert history.redo().label == "c"
    assert seen == ["-c", "+c"]

    history.undo()  # c is undone and sitting in the redo stack...
    history.push(step("d"))  # ...and a new operation throws that branch away
    assert history.redo() is None
    assert history.undo().label == "d"


def test_undoing_an_empty_history_is_not_an_error():
    history = undo.History()
    assert history.undo() is None
    assert history.redo() is None


# ── delete: the dialog ──────────────────────────────────────────────────────


async def test_delete_asks_first_and_cancelling_changes_nothing(app, papis_lib):
    from ptui import ui

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        assert isinstance(app.screen, ui.ConfirmDelete)

        await press(pilot, "escape")
        await settle(pilot)
        assert len(app.docs) == 3
        assert (papis_lib / "lib" / "doc0" / "info.yaml").exists()
        assert not app.history.done


async def test_the_dialog_confirms_against_every_mark_not_the_visible_ones(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space", "space", "space")  # mark all three
        await press(pilot, "/", *"attention")  # ...then narrow to one
        await settle(pilot)
        assert len(app.rows) == 1

        await press(pilot, "escape")  # leave the prompt, keep the narrow
        await press(pilot, "d", "d")
        summary = "\n".join(app.screen.summary)
        assert "Delete 3 document(s)" in app.screen.title_text
        assert "He2016" in summary and "Bengio2003" in summary
        await press(pilot, "escape")


async def test_a_file_outside_the_managed_roots_is_offered_unchecked(app, papis_lib):
    outside = papis_lib / "elsewhere"
    outside.mkdir()
    stray = outside / "stray.pdf"
    stray.write_bytes(b"pdf")
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    info.write_text(info.read_text() + f"files:\n- {stray}\n")
    managed = _attach(papis_lib, name="managed.pdf")

    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        choices = {Path(choice.label).name: choice for choice in app.screen.files}
        assert choices["managed.pdf"].checked  # under pdf_root
        assert not choices["stray.pdf"].checked
        assert "outside the managed roots" in choices["stray.pdf"].note
        await press(pilot, "escape")
    assert stray.exists() and managed.exists()


async def test_a_file_two_documents_share_is_never_checked_by_default(app, papis_lib):
    shared = _attach(papis_lib, "doc0", "shared.pdf")
    info = papis_lib / "lib" / "doc1" / "info.yaml"
    info.write_text(info.read_text() + f"files:\n- {shared}\n")

    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        choice = app.screen.files[0]
        assert not choice.checked  # under pdf_root, but not ours alone
        assert "also in He2016" in choice.note
        await press(pilot, "escape")


async def test_files_inside_the_folder_are_not_offered_they_travel_with_it(app, papis_lib):
    _attach(papis_lib, "doc0", "notes.pdf", outside=False)

    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        assert app.screen.files == []  # nothing to decide
        assert any("1 file(s) inside the folders" in line for line in app.screen.summary)
        await press(pilot, "escape")


# ── delete: trash strategy ──────────────────────────────────────────────────


async def test_delete_trashes_the_folder_and_undo_puts_it_back(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.current["title"] == "Attention Is All You Need"
        await press(pilot, "d", "d", "enter")
        await settle(pilot)

        assert len(app.docs) == 2
        assert not (papis_lib / "lib" / "doc0").exists()
        assert (papis_lib / "trash" / "doc0" / "info.yaml").exists()
        assert "Attention Is All You Need" not in [d["title"] for d in app.docs]

        await press(pilot, "u")
        await settle(pilot)
        assert (papis_lib / "lib" / "doc0" / "info.yaml").exists()
        assert len(app.docs) == 3
        assert "Attention Is All You Need" in [d["title"] for d in app.docs]


async def test_redo_deletes_it_again(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d", "enter")
        await settle(pilot)
        await press(pilot, "u")
        await settle(pilot)
        assert len(app.docs) == 3

        # Redo means *that* delete again, not "delete whatever is under the cursor".
        await press(pilot, "G")
        await press(pilot, "ctrl+r")
        await settle(pilot)
        assert len(app.docs) == 2
        assert not (papis_lib / "lib" / "doc0").exists()
        assert (papis_lib / "lib" / "doc2").exists()
        assert len(app.history.done) == 1  # the redo is the same step, not a new one

        await press(pilot, "u")
        await settle(pilot)
        assert (papis_lib / "lib" / "doc0" / "info.yaml").exists()  # and it undoes again


async def test_redo_finds_the_document_outside_the_scope(app, papis_lib):
    from ptui import actions

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d", "enter")
        await settle(pilot)
        await press(pilot, "u")
        await settle(pilot)

        actions.scope(app, "title:residual")  # doc0 is no longer in app.docs
        await settle(pilot)
        await press(pilot, "ctrl+r")
        await settle(pilot)
        assert not (papis_lib / "lib" / "doc0").exists()
        assert (papis_lib / "trash" / "doc0" / "info.yaml").exists()


async def test_a_checked_file_is_trashed_and_comes_back_with_undo(app, papis_lib):
    pdf = _attach(papis_lib, "doc0", "paper.pdf")

    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        assert app.screen.files[0].checked
        await press(pilot, "enter")
        await settle(pilot)
        assert not pdf.exists()
        assert (papis_lib / "trash" / "paper.pdf").read_bytes() == b"pdf"

        await press(pilot, "u")
        await settle(pilot)
        assert pdf.read_bytes() == b"pdf"


async def test_an_unchecked_file_survives_the_delete(app, papis_lib):
    pdf = _attach(papis_lib, "doc0", "paper.pdf")

    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        await press(pilot, "space")  # uncheck it
        await press(pilot, "enter")
        await settle(pilot)
        assert not (papis_lib / "lib" / "doc0").exists()  # the document went
        assert pdf.read_bytes() == b"pdf"  # the file stayed


async def test_deleting_a_marked_batch_is_one_undo_step(app, papis_lib):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space", "space")  # doc0 and doc1
        await press(pilot, "d", "d", "enter")
        await settle(pilot)
        assert len(app.docs) == 1
        assert len(app.history.done) == 1  # one operation, not two

        await press(pilot, "u")
        await settle(pilot)
        assert len(app.docs) == 3


# ── delete: none strategy ───────────────────────────────────────────────────


async def test_strategy_none_still_trashes_but_offers_no_undo(papis_lib, monkeypatch):
    app = build_app(papis_lib, extra='strategy = "none"\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        monkeypatch.setattr(app, "log_line", logged.append)
        await press(pilot, "d", "d", "enter")
        await settle(pilot)

        assert len(app.docs) == 2
        # SPEC: files always route through trash, whatever the strategy says
        assert (papis_lib / "trash" / "doc0" / "info.yaml").exists()
        assert not app.history.done

        await press(pilot, "u")
        assert any("nothing to undo" in message for message in logged)


# ── delete: git strategy ────────────────────────────────────────────────────


def _git(repo, *args):
    import subprocess

    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


async def test_strategy_git_commits_the_delete_and_undo_reverts_it(git_lib):
    repo = git_lib / "lib"
    app = build_app(git_lib, extra='strategy = "git"\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d", "enter")
        await settle(pilot)

        assert len(app.docs) == 2
        assert not (repo / "doc0").exists()
        assert _git(repo, "log", "-1", "--format=%s") == "ptui: delete 1 document(s)"
        assert _git(repo, "status", "--porcelain") == ""  # the delete is committed, not staged

        await press(pilot, "u")
        await settle(pilot)
        assert (repo / "doc0" / "info.yaml").exists()
        assert len(app.docs) == 3
        # revert, never reset: the delete stays in the log with its undo on top
        assert _git(repo, "log", "--format=%s").splitlines()[:2] == [
            "ptui: undo delete 1 document(s)",
            "ptui: delete 1 document(s)",
        ]


async def test_strategy_git_makes_one_commit_for_a_whole_batch(git_lib):
    repo = git_lib / "lib"
    app = build_app(git_lib, extra='strategy = "git"\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space", "space")
        await press(pilot, "d", "d", "enter")
        await settle(pilot)
        assert len(app.docs) == 1
        assert _git(repo, "log", "-1", "--format=%s") == "ptui: delete 2 document(s)"
        assert len(_git(repo, "log", "--format=%s").splitlines()) == 2  # library + delete


async def test_git_refuses_to_revert_a_commit_that_is_not_ptuis(git_lib):
    repo = git_lib / "lib"
    (repo / "doc0" / "info.yaml").write_text("title: hand edited\npapis_id: id0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "by hand")

    with pytest.raises(RuntimeError, match="not a ptui commit"):
        undo.git_revert(repo, _git(repo, "rev-parse", "HEAD"))


async def test_git_strategy_warns_when_the_pdf_root_is_not_tracked(git_lib, monkeypatch):
    app = build_app(git_lib, extra='strategy = "git"\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        # the warning is written during on_mount, before any monkeypatch could
        # see it, so ask the pane instead
        from textual.widgets import RichLog

        await press(pilot, "4")
        text = "\n".join("".join(s.text for s in strip._segments) for strip in app.query_one(RichLog).lines)
        assert "does not track" in text


# ── metadata undo, any strategy ─────────────────────────────────────────────


@pytest.mark.parametrize("strategy", ["trash", "git", "none"])
async def test_a_field_edit_is_undoable_under_every_strategy(git_lib, strategy):
    app = build_app(git_lib, extra=f'strategy = "{strategy}"\n')
    async with app.run_test() as pilot:
        await settle(pilot)
        assert app.current["year"] == 2017
        await press(pilot, "c", "f", *"year 1999", "enter")
        await settle(pilot)
        assert app.current["year"] == 1999

        await press(pilot, "u")
        await settle(pilot)
        assert app.current["year"] == 2017

        await press(pilot, "ctrl+r")
        await settle(pilot)
        assert app.current["year"] == 1999


async def test_undoing_a_tag_restores_the_absence_of_the_key(app):
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "c", "T", *"test", "enter")  # the fixture's only tag
        await settle(pilot)
        assert "tags" not in app.current

        await press(pilot, "u")
        await settle(pilot)
        assert app.current["tags"] == ["test"]


async def test_empty_trash_deletes_only_what_is_older_than_the_retention(app, papis_lib):
    """By the time it went in, not by its mtime: a moved folder keeps the mtime
    of its last edit, which would make a delete from today look ancient."""
    import json
    import os

    from ptui import place

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d", "enter")  # doc0 goes to the trash today
        await press(pilot, "d", "d", "enter")
        trash = papis_lib / "trash"
        assert sorted(p.name for p in trash.iterdir() if p.name != place.TRASH_INDEX) == ["doc0", "doc1"]
        os.utime(trash / "doc0", (0, 0))  # an ancient mtime must not matter
        index = json.loads((trash / place.TRASH_INDEX).read_text())
        index["doc1"] -= 40 * 86400  # but this one really went in 40 days ago
        (trash / place.TRASH_INDEX).write_text(json.dumps(index))

        await press(pilot, ":", *"lib.empty_trash", "enter", "enter")  # empty line: the configured 30 days
        await press(pilot, "enter")  # confirm
        assert (trash / "doc0").exists()
        assert not (trash / "doc1").exists()
        assert "doc1" not in json.loads((trash / place.TRASH_INDEX).read_text())


async def test_an_entry_that_climbs_into_pdf_root_is_managed_and_checked(papis_lib):
    """`../../pdfs/x.pdf` lives under `pdf_root` once normalised, so the dialog
    treats it like the absolute spelling: offered, and ticked by default."""
    root = papis_lib / "pdfs"
    root.mkdir()
    (root / "x.pdf").write_bytes(b"pdf")
    info = papis_lib / "lib" / "doc0" / "info.yaml"
    info.write_text(info.read_text() + "files:\n- ../../pdfs/x.pdf\n")
    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "d", "d")
        (choice,) = app.screen.files
        assert Path(choice.label) == root / "x.pdf"
        assert choice.checked
        await press(pilot, "escape")


async def test_a_file_shared_with_a_document_outside_the_scope_is_not_checked(papis_lib):
    """The other user of a file is looked for in the library, not the scope."""
    root = papis_lib / "pdfs"
    root.mkdir()
    shared = root / "shared.pdf"
    shared.write_bytes(b"pdf")
    for doc in ("doc0", "doc2"):
        info = papis_lib / "lib" / doc / "info.yaml"
        info.write_text(info.read_text() + f"files:\n- {shared}\n")
    app = build_app(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "s", *"year:2017", "enter")  # doc2 (2003) is out of scope
        await settle(pilot)
        assert [d["papis_id"] for d in app.docs] == ["id0"]
        await press(pilot, "d", "d")
        (choice,) = app.screen.files
        assert not choice.checked
        assert "Bengio2003" in choice.note
        await press(pilot, "escape")


@pytest.mark.parametrize("repository", [False, True])
def test_git_on_a_library_outside_git_starts_on_trash_and_says_so(request, papis_lib, monkeypatch, repository):
    """Decided: a strategy that cannot work is replaced at startup, like any
    config value that cannot, rather than failing at the first delete."""
    from click.testing import CliRunner

    from ptui import cli, config
    from ptui.app import PtuiApp

    if repository:
        request.getfixturevalue("git_lib")
    (papis_lib / "ptui").mkdir()
    (papis_lib / "ptui" / "config.toml").write_text('[undo]\nstrategy = "git"\n')
    monkeypatch.setattr(config, "config_dir", lambda: papis_lib / "ptui")
    seen = {}
    monkeypatch.setattr(PtuiApp, "run", lambda self, **_: seen.update(cfg=self.cfg))
    result = CliRunner().invoke(cli.main, [])
    assert result.exit_code == 0, result.output
    assert seen["cfg"].get("undo.strategy") == ("git" if repository else "trash")
    assert any("not in a git work tree" in problem for problem in seen["cfg"].invalid) is not repository


async def test_config_set_to_git_outside_git_keeps_the_trash(app):
    from ptui import actions

    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        app.log_line = logged.append
        actions.config_set(app, "undo.strategy", "git")
        assert app.cfg.get("undo.strategy") == "trash"
        assert any("not in a git work tree" in line for line in logged)


@pytest.mark.parametrize("strategy", ["trash", "git"])
async def test_a_merge_is_one_undo_step_and_redo_repeats_it(request, papis_lib, strategy):
    """`u` after `m m` brings the folded-in folder back from the trash (and into
    papis's index), the survivor's old fields and files, and the file the merge
    moved into the survivor's folder; `ctrl+r` merges again."""
    from ptui import actions, merge

    if strategy == "git":
        request.getfixturevalue("git_lib")
    lib = papis_lib / "lib"
    (lib / "doc1" / "supp.djvu").write_bytes(b"djvu")
    info = lib / "doc1" / "info.yaml"
    info.write_text(info.read_text() + "files:\n- supp.djvu\n")
    if strategy == "git":
        subprocess.run(["git", "add", "-A"], cwd=lib, check=True)
        subprocess.run(["git", "commit", "-qm", "attach"], cwd=lib, check=True)
    survivor_text = (lib / "doc0" / "info.yaml").read_text()

    app = build_app(papis_lib, extra=f'strategy = "{strategy}"\n')
    async with app.run_test() as pilot:
        actions.reload(app, rescan=True)
        await settle(pilot)

        def merged() -> bool:
            kept = next(d for d in app.docs if d["papis_id"] == "id0")
            return (
                not (lib / "doc1").exists()
                and kept.get("files") == ["supp.djvu"]
                and (lib / "doc0" / "supp.djvu").read_bytes() == b"djvu"
                and {d["papis_id"] for d in app.docs} == {"id0", "id2"}
            )

        survivor, loser = (next(d for d in app.docs if d["papis_id"] == i) for i in ("id0", "id1"))
        actions._merge_apply(app, merge.plan(survivor, [loser]), {})
        await settle(pilot)
        assert merged()

        await press(pilot, "u")
        assert (lib / "doc1" / "supp.djvu").read_bytes() == b"djvu"
        assert not (lib / "doc0" / "supp.djvu").exists()
        assert (lib / "doc0" / "info.yaml").read_text() == survivor_text
        assert {d["papis_id"] for d in app.docs} == {"id0", "id1", "id2"}
        if strategy == "git":
            status = subprocess.run(["git", "status", "--porcelain"], cwd=lib, capture_output=True, text=True)
            assert status.stdout == ""  # the work tree is as it was before the merge

        await press(pilot, "ctrl+r")
        assert merged()
