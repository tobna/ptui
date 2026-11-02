"""Verbs run after `info.yaml` changed on disk since ptui loaded the document — a
script, a sync, `papis` on the command line. Each must build what it writes from
the file, not from the copy in memory, and undo must put back what the file held.

The outside edit lands right before the verb, with no redraw in between: a
redraw re-reads a document it finds stale (TODO H.51), which would hide the bug.
"""

import os

import papis.config
import pytest
import yaml
from conftest import press, settle
from cover_helpers import attach, info

from ptui import actions, library, merge, safewrite, ui

SCHEME = "{doc[year]}-{doc[author_list][0][family]}"


def _outside(papis_lib, old: str, new: str, folder: str = "doc0") -> None:
    """What a script does: rewrite the file behind ptui's back, with a distinct mtime."""
    path = info(papis_lib, folder)
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def _disk(papis_lib, key: str, folder: str = "doc0"):
    return yaml.safe_load(info(papis_lib, folder).read_text()).get(key)


def _script_attaches(papis_lib, after: str, name: str = "c.pdf") -> None:
    (papis_lib / "lib" / "doc0" / name).write_bytes(name.encode())
    _outside(papis_lib, f"- {after}\n", f"- {after}\n- {name}\n")


async def test_detach_keeps_a_file_a_script_attached(app, papis_lib):
    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        _script_attaches(papis_lib, "b.pdf")
        actions.files_detach(app, 0)
        assert _disk(papis_lib, "files") == ["b.pdf", "c.pdf"]
        await press(pilot, "u")
        assert _disk(papis_lib, "files") == ["a.pdf", "b.pdf", "c.pdf"]


async def test_reorder_keeps_a_file_a_script_attached(app, papis_lib):
    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        _script_attaches(papis_lib, "b.pdf")
        actions.files_reorder(app, 0, 1)
        assert _disk(papis_lib, "files") == ["b.pdf", "a.pdf", "c.pdf"]
        await press(pilot, "u")
        assert _disk(papis_lib, "files") == ["a.pdf", "b.pdf", "c.pdf"]


async def test_repoint_keeps_a_file_a_script_attached(app, papis_lib):
    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    other = papis_lib / "lib" / "doc0" / "other.pdf"
    other.write_bytes(b"other")
    async with app.run_test() as pilot:
        await settle(pilot)
        _script_attaches(papis_lib, "b.pdf")
        actions.files_repoint(app, 0, str(other))
        assert _disk(papis_lib, "files") == ["other.pdf", "b.pdf", "c.pdf"]
        await press(pilot, "u")
        assert _disk(papis_lib, "files") == ["a.pdf", "b.pdf", "c.pdf"]


async def test_rename_keeps_a_file_a_script_attached(app, papis_lib):
    attach(papis_lib, "doc0", "paper.pdf", "b.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        papis.config.set("add-file-name", SCHEME)
        _script_attaches(papis_lib, "b.pdf")
        actions.files_rename(app, 0)
        assert _disk(papis_lib, "files") == ["2017-vaswani.pdf", "b.pdf", "c.pdf"]
        await press(pilot, "u")
        assert _disk(papis_lib, "files") == ["paper.pdf", "b.pdf", "c.pdf"]


async def test_normalize_keeps_a_file_a_script_attached(app, papis_lib):
    attach(papis_lib, "doc0", "a.txt")  # not a PDF: the fixture's catch-all rule keeps the style
    absolute = str(papis_lib / "lib" / "doc0" / "a.txt")
    path = info(papis_lib)
    path.write_text(path.read_text().replace("- a.txt\n", f"- {absolute}\n"))
    async with app.run_test() as pilot:
        await settle(pilot)
        _script_attaches(papis_lib, absolute, "c.txt")
        actions.files_normalize(app)
        assert _disk(papis_lib, "files") == ["a.txt", "c.txt"]
        await press(pilot, "u")
        assert _disk(papis_lib, "files") == [absolute, "c.txt"]


async def test_the_edit_form_starts_from_the_file_and_undo_puts_the_file_back(app, papis_lib):
    async with app.run_test(size=(100, 40)) as pilot:
        await settle(pilot)
        _outside(papis_lib, "tags: [test]", "tags: [test, from-script]")
        _outside(papis_lib, "year: 2017", "year: 2018")
        await press(pilot, "E")
        form = app.screen
        assert isinstance(form, ui.EditForm)
        assert form.query_one("#add-tags").value == "test, from-script"
        form.query_one("#add-tags").value += ", mine"
        form.query_one("#add-year").value = "2019"
        await press(pilot, "enter")
        assert _disk(papis_lib, "tags") == ["test", "from-script", "mine"]
        assert _disk(papis_lib, "year") == 2019
        await press(pilot, "u")
        assert _disk(papis_lib, "tags") == ["test", "from-script"]
        assert _disk(papis_lib, "year") == 2018


async def test_refresh_asks_before_replacing_a_value_a_script_wrote(app, papis_lib):
    """The fetched DOI fills a gap only while there is one: a DOI the file gained
    since loading is a disagreement, and a disagreement is a question."""
    fetched = {"title": "Attention Is All You Need", "year": 2017, "doi": "10.1/fetched"}
    async with app.run_test() as pilot:
        await settle(pilot)
        _outside(papis_lib, "ref: Vaswani2017\n", "ref: Vaswani2017\ndoi: 10.1/from-script\n")
        actions._refresh_merge(app, app.current, "doi", fetched)
        assert isinstance(app.screen, ui.SelectList)  # doi: which one?
        await press(pilot, "enter")  # this document's
        assert _disk(papis_lib, "doi") == "10.1/from-script"


async def test_backfill_keeps_a_date_a_script_wrote_while_it_asked(app, papis_lib):
    for folder in ("doc0", "doc1"):
        path = info(papis_lib, folder)
        path.write_text("".join(line for line in path.read_text().splitlines(True) if "time-added" not in line))
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.lib_backfill_dates(app)  # plans doc0 and doc1, then asks
        _outside(papis_lib, "papis_id: id0\n", "papis_id: id0\ntime-added: 2020-05-05-00:00:00\n")
        await press(pilot, "enter")
        assert _disk(papis_lib, "time-added") == "2020-05-05-00:00:00"
        assert _disk(papis_lib, "time-added", "doc1")  # the other one is stamped


def _race(monkeypatch, papis_lib, old: str, new: str) -> None:
    """A script writes between safewrite's read and its write — once."""
    real = safewrite.write

    def write(info_file) -> None:
        monkeypatch.setattr(safewrite, "write", real)
        _outside(papis_lib, old, new)
        real(info_file)

    monkeypatch.setattr(safewrite, "write", write)


def _doc(app, papis_id: str):
    return next(d for d in app.docs if library.doc_id(d) == papis_id)


REFUSED = [
    pytest.param(lambda app: actions.doc_set(app, "note", "mine"), lambda lib: _disk(lib, "note") == "mine", id="c f"),
    pytest.param(lambda app: actions.files_detach(app, 0), lambda lib: _disk(lib, "files") == ["b.pdf"], id="x"),
    pytest.param(
        lambda app: actions.cmdline_run(app, "doctor.fix", "refs"), lambda lib: _disk(lib, "ref") == "badref!", id="! f"
    ),
    pytest.param(  # the refused write takes the moved file back, too
        actions.files_relocate,
        lambda lib: (
            _disk(lib, "files")[0].startswith(str(lib / "pdfs")) and not (lib / "lib" / "doc0" / "a.pdf").exists()
        ),
        id="f r",
    ),
    pytest.param(
        lambda app: actions._touch_opened_at(app, app.current), lambda lib: bool(_disk(lib, "opened_at")), id="o"
    ),
    pytest.param(
        lambda app: actions._merge_apply(app, merge.plan(_doc(app, "id0"), [_doc(app, "id1")]), {}),
        lambda lib: not (lib / "lib" / "doc1").exists(),
        id="m m",
    ),
]


@pytest.mark.parametrize(("verb", "done"), REFUSED)
async def test_a_refused_write_rereads_the_file_so_doing_it_again_works(app, papis_lib, monkeypatch, verb, done):
    """`r` re-asked papis's cache, which never saw the outside edit (TODO H.57)."""
    from cover_helpers import logs

    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    path = info(papis_lib)
    path.write_text(path.read_text().replace("ref: Vaswani2017", "ref: bad ref!"))  # for `! f`
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = logs(app, monkeypatch)
        _race(monkeypatch, papis_lib, "tags: [test]", "tags: [test, from-script]")
        verb(app)
        assert any("changed on disk; reloaded from disk; do it again" in line for line in logged)
        assert not done(papis_lib)
        assert _doc(app, "id0")["tags"] == ["test", "from-script"]  # the file, read right there

        verb(app)
        assert done(papis_lib)
        assert _disk(papis_lib, "tags") == ["test", "from-script"]


UNDONE = REFUSED[:4]  # c f, x, ! f, f r: the verbs with an undo step


@pytest.mark.parametrize(("verb", "done"), UNDONE)
async def test_an_undo_that_meets_a_changed_file_stays_for_u_to_try_again(app, papis_lib, monkeypatch, verb, done):
    """It said `_reread`'s "do it again", which after `u` reads as running the
    verb again, and the step had gone to the redo stack: `u` again undid the
    step before it (TODO H.70)."""
    from cover_helpers import logs

    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    path = info(papis_lib)
    path.write_text(path.read_text().replace("ref: Vaswani2017", "ref: bad ref!"))  # for `! f`
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_tag(app, "earlier")  # the step a wrong `u` undoes
        verb(app)
        assert done(papis_lib)
        logged = logs(app, monkeypatch)
        _race(monkeypatch, papis_lib, "year: 2017", "year: 2018")
        await press(pilot, "u")
        assert done(papis_lib)  # not undone, files and entries alike
        assert any("undo did not happen" in line and "press u to try again" in line for line in logged)
        assert not any("do it again" in line for line in logged)

        await press(pilot, "u")
        assert not done(papis_lib)
        assert "earlier" in _disk(papis_lib, "tags")
        assert _disk(papis_lib, "year") == 2018  # the script's edit survives the undo


@pytest.mark.parametrize(("verb", "done"), [*UNDONE, REFUSED[5]])  # and m m
async def test_a_redo_that_meets_a_changed_file_stays_for_ctrl_r_to_try_again(app, papis_lib, monkeypatch, verb, done):
    """`f r` and `m m` rerun on redo, and a refused write ended the rerun in a
    RuntimeError: the step was dropped, so `ctrl+r` could not try again (TODO H.74)."""
    from cover_helpers import logs

    attach(papis_lib, "doc0", "a.pdf", "b.pdf")
    path = info(papis_lib)
    path.write_text(path.read_text().replace("ref: Vaswani2017", "ref: bad ref!"))
    async with app.run_test() as pilot:
        await settle(pilot)
        verb(app)
        await press(pilot, "u")
        assert not done(papis_lib)
        logged = logs(app, monkeypatch)
        _race(monkeypatch, papis_lib, "year: 2017", "year: 2018")
        await press(pilot, "ctrl+r")
        assert not done(papis_lib)
        assert any("redo did not happen" in line and "press ctrl+r to try again" in line for line in logged)

        await press(pilot, "ctrl+r")
        assert done(papis_lib)


def _pdfs(papis_lib) -> dict[str, bytes]:
    """The PDF root as it is on disk: each file's name and bytes."""
    root = papis_lib / "pdfs"
    return {path.name: path.read_bytes() for path in root.iterdir()} if root.exists() else {}


async def test_a_relocate_redo_retried_moves_only_what_the_refused_try_left(app, papis_lib, monkeypatch):
    """The retry relocates the document that was refused, and leaves alone the
    one the first try relocated; one `u` then puts both back (TODO H.74)."""
    attach(papis_lib, "doc0", "a.pdf")
    attach(papis_lib, "doc1", "b.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        await press(pilot, "f", "r", "enter")
        relocated = _pdfs(papis_lib)
        assert sorted(relocated.values()) == [b"a.pdf", b"b.pdf"]
        await press(pilot, "u")
        assert not _pdfs(papis_lib)

        _race(monkeypatch, papis_lib, "tags: [test]", "tags: [test, from-script]")  # doc0, the first written
        await press(pilot, "ctrl+r")
        assert (papis_lib / "lib" / "doc0" / "a.pdf").exists()  # refused: its file went back
        assert list(_pdfs(papis_lib).values()) == [b"b.pdf"]  # doc1's went ahead
        placed = next((papis_lib / "pdfs").iterdir())
        first = placed.stat()

        await press(pilot, "ctrl+r")
        assert _pdfs(papis_lib) == relocated
        assert not (papis_lib / "lib" / "doc0" / "a.pdf").exists()
        assert not (papis_lib / "lib" / "doc1" / "b.pdf").exists()
        again = placed.stat()
        assert (again.st_ino, again.st_ctime_ns) == (first.st_ino, first.st_ctime_ns)  # not moved again
        assert [_disk(papis_lib, "files", folder) for folder in ("doc0", "doc1")] == [
            [str(papis_lib / "pdfs" / name)] for name in sorted(relocated, key=relocated.get)
        ]

        await press(pilot, "u")  # one step, both tries
        assert not _pdfs(papis_lib)
        assert (papis_lib / "lib" / "doc0" / "a.pdf").read_bytes() == b"a.pdf"
        assert (papis_lib / "lib" / "doc1" / "b.pdf").read_bytes() == b"b.pdf"
        assert [_disk(papis_lib, "files", folder) for folder in ("doc0", "doc1")] == [["a.pdf"], ["b.pdf"]]
        assert _disk(papis_lib, "tags") == ["test", "from-script"]  # the script's edit survives


async def test_a_merge_redo_retried_moves_each_file_once(app, papis_lib, monkeypatch):
    """A refused survivor write takes the files back, so the retry starts from
    where the undo left things and places each of them once (TODO H.74)."""
    attach(papis_lib, "doc1", "b.pdf")
    loser_file = papis_lib / "lib" / "doc1" / "b.pdf"
    async with app.run_test() as pilot:
        await settle(pilot)
        actions._merge_apply(app, merge.plan(_doc(app, "id0"), [_doc(app, "id1")]), {})
        merged = _pdfs(papis_lib)
        assert list(merged.values()) == [b"b.pdf"]
        await press(pilot, "u")
        assert loser_file.exists() and not _pdfs(papis_lib)

        _race(monkeypatch, papis_lib, "tags: [test]", "tags: [test, from-script]")
        await press(pilot, "ctrl+r")
        assert loser_file.read_bytes() == b"b.pdf"  # back where it was, and nothing left in the root
        assert not _pdfs(papis_lib)

        await press(pilot, "ctrl+r")
        assert _pdfs(papis_lib) == merged  # one copy, under the one name
        assert not (papis_lib / "lib" / "doc1").exists()
        assert _disk(papis_lib, "files") == [str(papis_lib / "pdfs" / name) for name in merged]
        assert _disk(papis_lib, "tags") == ["test", "from-script"]

        await press(pilot, "u")
        assert loser_file.read_bytes() == b"b.pdf" and not _pdfs(papis_lib)


BATCH = [
    pytest.param(
        lambda app: actions.doc_set(app, "note", "mine"),
        lambda lib, folder: _disk(lib, "note", folder) == "mine",
        id="c f",
    ),
    pytest.param(actions.files_relocate, lambda lib, folder: not (lib / "lib" / folder / "a.pdf").exists(), id="f r"),
]


@pytest.mark.parametrize(("verb", "done"), BATCH)
async def test_a_batch_undo_or_redo_partly_done_says_how_much_and_what_is_left(app, papis_lib, monkeypatch, verb, done):
    """With one document of a batch changed on disk, the others were undone,
    but the log said "undo did not happen" (TODO H.75)."""
    from cover_helpers import logs

    for folder in ("doc0", "doc1"):
        attach(papis_lib, folder, "a.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        verb(app)
        await press(pilot, "enter")  # the batch confirm
        assert done(papis_lib, "doc0") and done(papis_lib, "doc1")
        logged = logs(app, monkeypatch)

        _race(monkeypatch, papis_lib, "papis_id: id0\n", "papis_id: id0\nscript: 1\n")  # doc0, the first written
        await press(pilot, "u")
        assert done(papis_lib, "doc0") and not done(papis_lib, "doc1")
        said = (
            "undo done on 1 document(s), but Vaswani2017 changed on disk; reloaded from disk; press u to undo the rest"
        )
        assert any(said in line for line in logged), logged
        await press(pilot, "u")
        assert not done(papis_lib, "doc0")

        _race(monkeypatch, papis_lib, "script: 1\n", "script: 2\n")
        await press(pilot, "ctrl+r")
        assert done(papis_lib, "doc1") and not done(papis_lib, "doc0")
        said = "redo done on 1 document(s), but Vaswani2017 changed on disk; reloaded from disk; press ctrl+r to redo the rest"
        assert any(said in line for line in logged), logged
        await press(pilot, "ctrl+r")
        assert done(papis_lib, "doc0") and done(papis_lib, "doc1")
        assert not any("did not happen" in line for line in logged)
        assert _disk(papis_lib, "script") == 2
