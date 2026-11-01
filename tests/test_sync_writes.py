"""Verbs run after `info.yaml` changed on disk since ptui loaded the document — a
script, a sync, `papis` on the command line. Each must build what it writes from
the file, not from the copy in memory, and undo must put back what the file held.

The outside edit lands right before the verb, with no redraw in between: a
redraw re-reads a document it finds stale (TODO H.51), which would hide the bug.
"""

import os

import papis.config
import yaml
from conftest import press, settle
from cover_helpers import attach, info

from ptui import actions, ui

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
