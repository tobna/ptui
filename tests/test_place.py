"""File placement: where a file goes, and that nothing is clobbered."""

import dataclasses

import papis.document
import pytest

from ptui import place

RULES = place.Rules(
    rules=(
        place.Rule(name="notes stay put", match=("*_notes.pdf",), operation="in-place"),
        place.Rule(
            name="pdfs to central root",
            match=("*.pdf",),
            operation="move",
            destination="{pdf_root}/{doc[year]}_{doc[author_list][0][family]}_{doc[title]}.pdf",
            path_style="absolute",
            slugify=True,
        ),
    ),
    default=place.Rule(name="everything else", match=("*",), operation="in-place"),
    pdf_root=None,
    collision="skip",
)


@pytest.fixture
def lib(tmp_path):
    """A document folder, a pdf root, and one loose pdf to place.

    Args:
        tmp_path: pytest's per-test temporary folder.

    Returns:
        `(document, loose file, rules, tmp_path)`.
    """
    folder = tmp_path / "doc"
    folder.mkdir()
    (tmp_path / "pdfs").mkdir()
    doc = papis.document.from_data(
        {
            "title": "Attention Is All You Need!",
            "year": 2017,
            "author_list": [{"family": "Vaswani", "given": "A"}],
        }
    )
    doc.set_folder(str(folder))
    source = folder / "downloaded file.pdf"
    source.write_bytes(b"pdf-bytes")
    rules = dataclasses.replace(RULES, pdf_root=tmp_path / "pdfs")
    return doc, source, rules, tmp_path


def test_moves_and_is_idempotent(lib):
    """Moves and is idempotent.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    doc, source, rules, tmp_path = lib
    result = place.place(doc, source, rules)
    assert result.status == "ok"
    assert result.destination == tmp_path / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    assert result.destination.read_bytes() == b"pdf-bytes"
    assert not source.exists()
    assert result.entry == str(result.destination)

    again = place.place(doc, result.destination, rules)
    assert again.status == "already"
    assert again.destination == result.destination


def test_in_place_rule_is_unmanaged(lib):
    """In place rule is unmanaged.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    doc, _, rules, _ = lib
    notes = doc.get_main_folder() + "/paper_notes.pdf"
    assert place.place(doc, notes, rules).status == "unmanaged"


def test_conflict_is_skipped_and_force_suffixes(lib):
    """Conflict is skipped and force suffixes.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    doc, source, rules, tmp_path = lib
    destination = tmp_path / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    destination.write_bytes(b"a different paper")

    result = place.place(doc, source, rules)
    assert result.status == "conflict"
    assert source.exists() and destination.read_bytes() == b"a different paper"

    forced = place.place(doc, source, rules, force=True)
    assert forced.status == "ok"
    assert forced.destination.name.endswith("_b.pdf")  # deterministic, not a timestamp


def test_identical_destination_is_a_duplicate(lib):
    """Identical destination is a duplicate.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    doc, source, rules, tmp_path = lib
    destination = tmp_path / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    destination.write_bytes(b"pdf-bytes")
    result = place.place(doc, source, rules)
    assert result.status == "duplicate"
    assert source.exists()  # nothing destroyed; the caller offers repoint + delete


def test_dry_run_touches_nothing(lib):
    """Dry run touches nothing.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    doc, source, rules, _ = lib
    result = place.place(doc, source, rules, dry_run=True)
    assert result.status == "ok"
    assert source.exists() and not result.destination.exists()


def test_rollback_restores_the_source(lib):
    """Rollback restores the source.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    doc, source, rules, _ = lib
    result = place.place(doc, source, rules)
    place.rollback(result)
    assert source.read_bytes() == b"pdf-bytes"
    assert not result.destination.exists()


def test_case_insensitive_collision_is_detected(tmp_path):
    """Case insensitive collision is detected.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "Foo.pdf").write_bytes(b"x")
    assert place._existing(tmp_path / "foo.pdf") == tmp_path / "Foo.pdf"
    assert place._existing(tmp_path / "bar.pdf") is None


def test_relative_entry_style_is_kept(lib):
    """Relative entry style is kept.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    doc, source, rules, _ = lib
    rules = dataclasses.replace(
        rules,
        rules=(
            place.Rule(name="r", match=("*.pdf",), operation="move", destination="{pdf_root}/x.pdf", path_style="keep"),
        ),
    )
    result = place.place(doc, source, rules, previous="downloaded file.pdf")
    assert result.entry == "../pdfs/x.pdf"


def test_same_file_sees_through_spelling_and_links(tmp_path):
    """Same file sees through spelling and links.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    real = tmp_path / "pdfs" / "x.pdf"
    real.parent.mkdir()
    real.write_bytes(b"pdf")
    link = tmp_path / "x-link.pdf"
    link.symlink_to(real)
    assert place.same_file(tmp_path / "lib" / "doc" / ".." / ".." / "pdfs" / "x.pdf", real)
    assert place.same_file(link, real)
    assert not place.same_file(tmp_path / "pdfs" / "y.pdf", real)


def test_a_relative_entry_never_climbs_to_the_root(lib):
    """A relative entry never climbs to the root.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    from pathlib import Path

    from loguru import logger

    doc, _source, _rules, tmp_path = lib
    logged = []
    sink = logger.add(logged.append, format="{message}")
    try:
        assert place._entry(tmp_path / "pdfs" / "x.pdf", doc, "relative-doc", None) == "../pdfs/x.pdf"
        assert not logged
        assert place._entry(Path("/nowhere-ptui/x.pdf"), doc, "relative-doc", None) == "/nowhere-ptui/x.pdf"
        assert "written absolute" in str(logged[0])
    finally:
        logger.remove(sink)


def test_a_broken_link_is_a_clash_for_suffixing_and_for_adopt(lib):
    """A broken link is a clash for suffixing and for adopt.

    Args:
        lib: The `lib` fixture: `(document, loose file, rules, tmp_path)`.
    """
    doc, source, rules, tmp_path = lib
    dead = tmp_path / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    dead.symlink_to(tmp_path / "gone.pdf")
    result = place.place(doc, source, dataclasses.replace(rules, collision="suffix"))
    assert result.status == "ok"
    assert result.destination.name == "2017_Vaswani_Attention_Is_All_You_Need_b.pdf"
    assert dead.is_symlink()  # the link is left as it was

    loose = tmp_path / "loose.djvu"
    loose.write_bytes(b"djvu")
    (tmp_path / "doc" / "loose.djvu").symlink_to(tmp_path / "gone.djvu")
    assert place.adopt(loose, tmp_path / "doc").name == "loose_b.djvu"


@pytest.mark.parametrize(
    ("value", "want"), [(None, []), ("", []), ("paper.pdf", ["paper.pdf"]), (["a.pdf", "b.pdf"], ["a.pdf", "b.pdf"])]
)
def test_entries_reads_a_lone_string_as_one_entry_as_papis_does(value, want):
    """Entries reads a lone string as one entry as papis does.

    Args:
        value: Parametrized, such as `None`.
        want: Parametrized, such as `[]`.
    """
    doc = papis.document.from_data({} if value is None else {"files": value})
    assert place.entries(doc) == want
