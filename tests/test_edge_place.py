"""Edges of `place()`: every op, every path style, collisions, the cross-device
path, and the trash index."""

import dataclasses
import errno
import json
import os
import time

import papis.document
import pytest

from ptui import place

DEST = "{pdf_root}/{doc[year]}_{doc[author_list][0][family]}.pdf"


def _rules(tmp_path, **rule):
    spec = {"name": "r", "match": ("*.pdf",), "op": "move", "dest": DEST, "path_style": "absolute"} | rule
    return place.Rules(
        rules=(place.Rule(**spec),),
        default=place.Rule(name="default", match=("*",)),
        pdf_root=tmp_path / "pdfs",
    )


@pytest.fixture
def doc(tmp_path):
    folder = tmp_path / "doc"
    folder.mkdir()
    (tmp_path / "pdfs").mkdir()
    doc = papis.document.from_data({"title": "T", "year": 2017, "author_list": [{"family": "Vaswani"}]})
    doc.set_folder(str(folder))
    (folder / "paper.pdf").write_bytes(b"pdf")
    return doc


def _src(doc):
    return place.resolve(doc, "paper.pdf")


@pytest.mark.parametrize(
    ("style", "previous", "relative"),
    [
        ("absolute", None, False),
        ("relative-doc", None, True),
        ("keep", None, False),  # a new entry has no style to keep: absolute
        ("keep", "paper.pdf", True),
        ("keep", "/somewhere/paper.pdf", False),
    ],
)
def test_the_entry_follows_the_path_style(doc, tmp_path, style, previous, relative):
    result = place.place(doc, _src(doc), _rules(tmp_path, path_style=style), previous=previous)
    assert result.status == "ok"
    assert result.entry == ("../pdfs/2017_Vaswani.pdf" if relative else str(tmp_path / "pdfs" / "2017_Vaswani.pdf"))
    assert place.resolve(doc, result.entry).read_bytes() == b"pdf"


def test_copy_keeps_the_source_and_rollback_removes_only_the_copy(doc, tmp_path):
    result = place.place(doc, _src(doc), _rules(tmp_path, op="copy"))
    assert result.status == "ok"
    assert _src(doc).read_bytes() == result.dest.read_bytes() == b"pdf"
    place.rollback(result)
    assert _src(doc).exists() and not result.dest.exists()


def test_hardlink_shares_the_inode(doc, tmp_path):
    result = place.place(doc, _src(doc), _rules(tmp_path, op="hardlink"))
    assert result.status == "ok"
    assert os.path.samefile(_src(doc), result.dest)
    assert not result.dest.is_symlink()


def test_link_is_a_symlink_back_to_the_source_and_placing_again_is_already(doc, tmp_path):
    rules = _rules(tmp_path, op="link")
    result = place.place(doc, _src(doc), rules)
    assert result.dest.is_symlink() and result.dest.resolve() == _src(doc)
    assert place.place(doc, _src(doc), rules).status == "already"
    place.rollback(result)
    assert _src(doc).read_bytes() == b"pdf" and not result.dest.is_symlink()


def test_placing_the_placed_file_again_keeps_its_relative_entry(doc, tmp_path):
    rules = _rules(tmp_path, path_style="keep")
    first = place.place(doc, _src(doc), rules, previous="paper.pdf")
    again = place.place(doc, first.entry, rules, previous=first.entry)
    assert (again.status, again.entry) == ("already", first.entry)


def test_collision_suffix_needs_no_force_and_hash_check_off_calls_a_twin_a_conflict(doc, tmp_path):
    taken = tmp_path / "pdfs" / "2017_Vaswani.pdf"
    taken.write_bytes(b"pdf")  # byte-identical
    rules = _rules(tmp_path)
    assert place.place(doc, _src(doc), rules, dry_run=True).status == "duplicate"

    blind = dataclasses.replace(rules, hash_check=False)
    assert place.place(doc, _src(doc), blind, dry_run=True).status == "conflict"

    suffixing = dataclasses.replace(blind, collision="suffix")
    result = place.place(doc, _src(doc), suffixing)
    assert result.dest.name == "2017_Vaswani_b.pdf"
    assert taken.read_bytes() == b"pdf"


def test_every_suffix_taken_is_a_conflict_not_a_clobber(doc, tmp_path):
    pdfs = tmp_path / "pdfs"
    for name in ["2017_Vaswani", *(f"2017_Vaswani_{c}" for c in place.SUFFIXES)]:
        (pdfs / f"{name}.pdf").write_bytes(b"other")
    result = place.place(doc, _src(doc), _rules(tmp_path), force=True)
    assert (result.status, result.message) == ("conflict", "no free suffix")
    assert _src(doc).exists()


def test_a_name_that_differs_only_in_case_is_a_clash(doc, tmp_path):
    (tmp_path / "pdfs" / "2017_VASWANI.pdf").write_bytes(b"other")
    rules = _rules(tmp_path)
    assert place.place(doc, _src(doc), rules).status == "conflict"
    forced = place.place(doc, _src(doc), rules, force=True)
    assert forced.dest.name == "2017_Vaswani_b.pdf"


def test_a_document_the_scheme_cannot_name_is_an_error_and_moves_nothing(doc, tmp_path):
    doc["author_list"] = []  # `[0][family]` has nothing to index
    result = place.place(doc, _src(doc), _rules(tmp_path))
    assert result.status == "error" and "cannot name" in result.message
    assert _src(doc).exists() and not list((tmp_path / "pdfs").iterdir())


def test_a_missing_source_is_an_error(doc, tmp_path):
    assert place.place(doc, "gone.pdf", _rules(tmp_path)).status == "error"


def test_cross_device_goes_through_a_tmp_name_and_leaves_no_tmp(doc, tmp_path, monkeypatch):
    real_link = os.link

    def link(src, dst):
        if not os.path.basename(src).startswith("."):  # the direct link, not the tmp's
            raise OSError(errno.EXDEV, "cross-device link")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", link)
    result = place.place(doc, _src(doc), _rules(tmp_path))
    assert result.status == "ok"
    assert result.dest.read_bytes() == b"pdf" and not _src(doc).exists()
    assert [p.name for p in (tmp_path / "pdfs").iterdir()] == ["2017_Vaswani.pdf"]


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a dangling symlink at the destination makes place() raise FileNotFoundError "
    "(os.path.samefile on the dead link) instead of returning a status",
)
def test_a_dangling_symlink_at_the_destination_is_a_conflict_not_a_crash(doc, tmp_path):
    (tmp_path / "pdfs" / "2017_Vaswani.pdf").symlink_to(tmp_path / "moved-away.pdf")
    result = place.place(doc, _src(doc), _rules(tmp_path))
    assert result.status in ("conflict", "error")
    assert _src(doc).exists()


def test_holding_finds_a_byte_identical_file_and_skips_missing_entries(doc, tmp_path):
    doc["files"] = ["gone.pdf", "paper.pdf"]
    loose = tmp_path / "loose.pdf"
    loose.write_bytes(b"pdf")
    assert place.holding(doc, loose) == _src(doc)
    loose.write_bytes(b"different")
    assert place.holding(doc, loose) is None


def test_trashing_one_name_twice_suffixes_and_stamps_both(tmp_path):
    trash = tmp_path / "trash"
    for _ in range(2):
        folder = tmp_path / "lib" / "doc"
        folder.mkdir(parents=True)
        (folder / "info.yaml").write_text("title: x\n")
        place.trash(folder, trash)
    assert sorted(p.name for p in trash.iterdir()) == [".ptui-trashed.json", "doc", "doc_b"]
    assert set(json.loads((trash / place.TRASH_INDEX).read_text())) == {"doc", "doc_b"}


def test_the_trash_index_forgets_what_left_and_stamps_what_it_never_saw(tmp_path):
    trash = tmp_path / "trash"
    (trash / "old").mkdir(parents=True)
    (trash / "unknown").mkdir()
    (trash / place.TRASH_INDEX).write_text(json.dumps({"old": 1.0, "restored": 2.0}))
    before = time.time()
    index = place.trashed_at(trash)
    assert index["old"] == 1.0  # a known stamp is kept, not refreshed
    assert "restored" not in index  # an undo moved it back
    assert index["unknown"] >= before  # full retention, never guessed low


def test_a_corrupt_trash_index_is_rebuilt(tmp_path):
    trash = tmp_path / "trash"
    (trash / "doc").mkdir(parents=True)
    (trash / place.TRASH_INDEX).write_text("{not json")
    assert set(place.trashed_at(trash)) == {"doc"}
    assert set(json.loads((trash / place.TRASH_INDEX).read_text())) == {"doc"}
