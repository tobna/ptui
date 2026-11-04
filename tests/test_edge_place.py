"""Edges of `place()`.

Every op, every path style, collisions, the cross-device path, and the trash index.
"""

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
    """Build a rule set with one rule for `*.pdf`.

    Args:
        tmp_path: pytest's per-test temporary folder.
        **rule: `Rule` fields overriding the moving rule's.

    Returns:
        The `Rules`.
    """
    spec = {"name": "r", "match": ("*.pdf",), "operation": "move", "destination": DEST, "path_style": "absolute"} | rule
    return place.Rules(
        rules=(place.Rule(**spec),),
        default=place.Rule(name="default", match=("*",)),
        pdf_root=tmp_path / "pdfs",
    )


@pytest.fixture
def doc(tmp_path):
    """Build a document with `paper.pdf` in its folder.

    Args:
        tmp_path: pytest's per-test temporary folder.

    Returns:
        The document.
    """
    folder = tmp_path / "doc"
    folder.mkdir()
    (tmp_path / "pdfs").mkdir()
    doc = papis.document.from_data({"title": "T", "year": 2017, "author_list": [{"family": "Vaswani"}]})
    doc.set_folder(str(folder))
    (folder / "paper.pdf").write_bytes(b"pdf")
    return doc


def _source_of(doc):
    """Find the document's `paper.pdf`.

    Args:
        doc: The document.

    Returns:
        The file's path.
    """
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
    """The entry follows the path style.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
        style: Parametrized, such as `'absolute'`.
        previous: Parametrized, such as `None`.
        relative: Parametrized, such as `False`.
    """
    result = place.place(doc, _source_of(doc), _rules(tmp_path, path_style=style), previous=previous)
    assert result.status == "ok"
    assert result.entry == ("../pdfs/2017_Vaswani.pdf" if relative else str(tmp_path / "pdfs" / "2017_Vaswani.pdf"))
    assert place.resolve(doc, result.entry).read_bytes() == b"pdf"


def test_copy_keeps_the_source_and_rollback_removes_only_the_copy(doc, tmp_path):
    """Copy keeps the source and rollback removes only the copy.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    result = place.place(doc, _source_of(doc), _rules(tmp_path, operation="copy"))
    assert result.status == "ok"
    assert _source_of(doc).read_bytes() == result.destination.read_bytes() == b"pdf"
    place.rollback(result)
    assert _source_of(doc).exists() and not result.destination.exists()


def test_rollback_keeps_the_placed_file_when_another_has_taken_its_old_name(doc, tmp_path):
    """Rollback keeps the placed file when another has taken its old name.

    `u` rolls back long after the move; deleting the placed file because a
    source exists would lose it whenever that source is a new file.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    result = place.place(doc, _source_of(doc), _rules(tmp_path))
    _source_of(doc).write_bytes(b"a new download under the old name")
    with pytest.raises(FileExistsError):
        place.rollback(result)
    assert result.destination.read_bytes() == b"pdf"
    assert _source_of(doc).read_bytes() == b"a new download under the old name"


def test_hardlink_shares_the_inode(doc, tmp_path):
    """Hardlink shares the inode.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    result = place.place(doc, _source_of(doc), _rules(tmp_path, operation="hardlink"))
    assert result.status == "ok"
    assert os.path.samefile(_source_of(doc), result.destination)
    assert not result.destination.is_symlink()


def test_link_is_a_symlink_back_to_the_source_and_placing_again_is_already(doc, tmp_path):
    """Link is a symlink back to the source and placing again is already.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    rules = _rules(tmp_path, operation="link")
    result = place.place(doc, _source_of(doc), rules)
    assert result.destination.is_symlink() and result.destination.resolve() == _source_of(doc)
    assert place.place(doc, _source_of(doc), rules).status == "already"
    place.rollback(result)
    assert _source_of(doc).read_bytes() == b"pdf" and not result.destination.is_symlink()


def test_placing_the_placed_file_again_keeps_its_relative_entry(doc, tmp_path):
    """Placing the placed file again keeps its relative entry.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    rules = _rules(tmp_path, path_style="keep")
    first = place.place(doc, _source_of(doc), rules, previous="paper.pdf")
    again = place.place(doc, first.entry, rules, previous=first.entry)
    assert (again.status, again.entry) == ("already", first.entry)


def test_collision_suffix_needs_no_force_and_hash_check_off_calls_a_twin_a_conflict(doc, tmp_path):
    """Collision suffix needs no force and hash check off calls a twin a conflict.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    taken = tmp_path / "pdfs" / "2017_Vaswani.pdf"
    taken.write_bytes(b"pdf")  # byte-identical
    rules = _rules(tmp_path)
    assert place.place(doc, _source_of(doc), rules, dry_run=True).status == "duplicate"

    blind = dataclasses.replace(rules, hash_check=False)
    assert place.place(doc, _source_of(doc), blind, dry_run=True).status == "conflict"

    suffixing = dataclasses.replace(blind, collision="suffix")
    result = place.place(doc, _source_of(doc), suffixing)
    assert result.destination.name == "2017_Vaswani_b.pdf"
    assert taken.read_bytes() == b"pdf"


def test_every_suffix_taken_is_a_conflict_not_a_clobber(doc, tmp_path):
    """Every suffix taken is a conflict not a clobber.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    pdfs = tmp_path / "pdfs"
    for name in ["2017_Vaswani", *(f"2017_Vaswani_{suffix}" for suffix in place.SUFFIXES)]:
        (pdfs / f"{name}.pdf").write_bytes(b"other")
    result = place.place(doc, _source_of(doc), _rules(tmp_path), force=True)
    assert (result.status, result.message) == ("conflict", "no free suffix")
    assert _source_of(doc).exists()


def test_a_name_that_differs_only_in_case_is_a_clash(doc, tmp_path):
    """A name that differs only in case is a clash.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "pdfs" / "2017_VASWANI.pdf").write_bytes(b"other")
    rules = _rules(tmp_path)
    assert place.place(doc, _source_of(doc), rules).status == "conflict"
    forced = place.place(doc, _source_of(doc), rules, force=True)
    assert forced.destination.name == "2017_Vaswani_b.pdf"


def test_a_document_the_scheme_cannot_name_is_an_error_and_moves_nothing(doc, tmp_path):
    """A document the scheme cannot name is an error and moves nothing.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    doc["author_list"] = []  # `[0][family]` has nothing to index
    result = place.place(doc, _source_of(doc), _rules(tmp_path))
    assert result.status == "error" and "cannot name" in result.message
    assert _source_of(doc).exists() and not list((tmp_path / "pdfs").iterdir())


def test_a_missing_source_is_an_error(doc, tmp_path):
    """A missing source is an error.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    assert place.place(doc, "gone.pdf", _rules(tmp_path)).status == "error"


def test_cross_device_goes_through_a_tmp_name_and_leaves_no_tmp(doc, tmp_path, monkeypatch):
    """Cross device goes through a tmp name and leaves no tmp.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
        monkeypatch: pytest's monkeypatch fixture.
    """
    real_link = os.link

    def link(source, destination):
        """Refuse the direct link, as another filesystem does, but not the temporary file's.

        Args:
            source: The file to link.
            destination: The new name.

        Raises:
            OSError: For the direct link.
        """
        if not os.path.basename(source).startswith("."):  # the direct link, not the tmp's
            raise OSError(errno.EXDEV, "cross-device link")
        real_link(source, destination)

    monkeypatch.setattr(os, "link", link)
    result = place.place(doc, _source_of(doc), _rules(tmp_path))
    assert result.status == "ok"
    assert result.destination.read_bytes() == b"pdf" and not _source_of(doc).exists()
    assert [path.name for path in (tmp_path / "pdfs").iterdir()] == ["2017_Vaswani.pdf"]


def test_a_dangling_symlink_at_the_destination_is_a_conflict_not_a_crash(doc, tmp_path):
    """A dangling symlink at the destination is a conflict not a crash.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "pdfs" / "2017_Vaswani.pdf").symlink_to(tmp_path / "moved-away.pdf")
    result = place.place(doc, _source_of(doc), _rules(tmp_path))
    assert result.status in ("conflict", "error")
    assert _source_of(doc).exists()


def test_holding_finds_a_byte_identical_file_and_skips_missing_entries(doc, tmp_path):
    """Holding finds a byte identical file and skips missing entries.

    Args:
        doc: The `doc` fixture: a document with `paper.pdf` in its folder.
        tmp_path: pytest's per-test temporary folder.
    """
    doc["files"] = ["gone.pdf", "paper.pdf"]
    loose = tmp_path / "loose.pdf"
    loose.write_bytes(b"pdf")
    assert place.holding(doc, loose) == _source_of(doc)
    loose.write_bytes(b"different")
    assert place.holding(doc, loose) is None


def test_trashing_one_name_twice_suffixes_and_stamps_both(tmp_path):
    """Trashing one name twice suffixes and stamps both.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    trash = tmp_path / "trash"
    for _ in range(2):
        folder = tmp_path / "lib" / "doc"
        folder.mkdir(parents=True)
        (folder / "info.yaml").write_text("title: x\n")
        place.trash(folder, trash)
    assert sorted(path.name for path in trash.iterdir()) == [".ptui-trashed.json", "doc", "doc_b"]
    assert set(json.loads((trash / place.TRASH_INDEX).read_text())) == {"doc", "doc_b"}


def test_the_trash_index_forgets_what_left_and_stamps_what_it_never_saw(tmp_path):
    """The trash index forgets what left and stamps what it never saw.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
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
    """A corrupt trash index is rebuilt.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    trash = tmp_path / "trash"
    (trash / "doc").mkdir(parents=True)
    (trash / place.TRASH_INDEX).write_text("{not json")
    assert set(place.trashed_at(trash)) == {"doc"}
    assert set(json.loads((trash / place.TRASH_INDEX).read_text())) == {"doc"}
