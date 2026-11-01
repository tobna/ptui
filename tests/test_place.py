import dataclasses

import papis.document
import pytest

from ptui import place

RULES = place.Rules(
    rules=(
        place.Rule(name="notes stay put", match=("*_notes.pdf",), op="in-place"),
        place.Rule(
            name="pdfs to central root",
            match=("*.pdf",),
            op="move",
            dest="{pdf_root}/{doc[year]}_{doc[author_list][0][family]}_{doc[title]}.pdf",
            path_style="absolute",
            slugify=True,
        ),
    ),
    default=place.Rule(name="everything else", match=("*",), op="in-place"),
    pdf_root=None,
    collision="skip",
)


@pytest.fixture
def lib(tmp_path):
    """A document folder, a pdf root, and one loose pdf to place."""
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
    src = folder / "downloaded file.pdf"
    src.write_bytes(b"pdf-bytes")
    rules = dataclasses.replace(RULES, pdf_root=tmp_path / "pdfs")
    return doc, src, rules, tmp_path


def test_moves_and_is_idempotent(lib):
    doc, src, rules, tmp_path = lib
    result = place.place(doc, src, rules)
    assert result.status == "ok"
    assert result.dest == tmp_path / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    assert result.dest.read_bytes() == b"pdf-bytes"
    assert not src.exists()
    assert result.entry == str(result.dest)

    again = place.place(doc, result.dest, rules)
    assert again.status == "already"
    assert again.dest == result.dest


def test_in_place_rule_is_unmanaged(lib):
    doc, _, rules, _ = lib
    notes = doc.get_main_folder() + "/paper_notes.pdf"
    assert place.place(doc, notes, rules).status == "unmanaged"


def test_conflict_is_skipped_and_force_suffixes(lib):
    doc, src, rules, tmp_path = lib
    dest = tmp_path / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    dest.write_bytes(b"a different paper")

    result = place.place(doc, src, rules)
    assert result.status == "conflict"
    assert src.exists() and dest.read_bytes() == b"a different paper"

    forced = place.place(doc, src, rules, force=True)
    assert forced.status == "ok"
    assert forced.dest.name.endswith("_b.pdf")  # deterministic, not a timestamp


def test_identical_destination_is_a_duplicate(lib):
    doc, src, rules, tmp_path = lib
    dest = tmp_path / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    dest.write_bytes(b"pdf-bytes")
    result = place.place(doc, src, rules)
    assert result.status == "duplicate"
    assert src.exists()  # nothing destroyed; the caller offers repoint + delete


def test_dry_run_touches_nothing(lib):
    doc, src, rules, _ = lib
    result = place.place(doc, src, rules, dry_run=True)
    assert result.status == "ok"
    assert src.exists() and not result.dest.exists()


def test_rollback_restores_the_source(lib):
    doc, src, rules, _ = lib
    result = place.place(doc, src, rules)
    place.rollback(result)
    assert src.read_bytes() == b"pdf-bytes"
    assert not result.dest.exists()


def test_case_insensitive_collision_is_detected(tmp_path):
    (tmp_path / "Foo.pdf").write_bytes(b"x")
    assert place._existing(tmp_path / "foo.pdf") == tmp_path / "Foo.pdf"
    assert place._existing(tmp_path / "bar.pdf") is None


def test_relative_entry_style_is_kept(lib):
    doc, src, rules, _ = lib
    rules = dataclasses.replace(
        rules,
        rules=(place.Rule(name="r", match=("*.pdf",), op="move", dest="{pdf_root}/x.pdf", path_style="keep"),),
    )
    result = place.place(doc, src, rules, previous="downloaded file.pdf")
    assert result.entry == "../pdfs/x.pdf"


def test_same_file_sees_through_spelling_and_links(tmp_path):
    real = tmp_path / "pdfs" / "x.pdf"
    real.parent.mkdir()
    real.write_bytes(b"pdf")
    link = tmp_path / "x-link.pdf"
    link.symlink_to(real)
    assert place.same_file(tmp_path / "lib" / "doc" / ".." / ".." / "pdfs" / "x.pdf", real)
    assert place.same_file(link, real)
    assert not place.same_file(tmp_path / "pdfs" / "y.pdf", real)


def test_a_relative_entry_never_climbs_to_the_root(lib):
    from pathlib import Path

    from loguru import logger

    doc, _src, _rules, tmp_path = lib
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
    doc, src, rules, tmp_path = lib
    dead = tmp_path / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    dead.symlink_to(tmp_path / "gone.pdf")
    result = place.place(doc, src, dataclasses.replace(rules, collision="suffix"))
    assert result.status == "ok"
    assert result.dest.name == "2017_Vaswani_Attention_Is_All_You_Need_b.pdf"
    assert dead.is_symlink()  # the link is left as it was

    loose = tmp_path / "loose.djvu"
    loose.write_bytes(b"djvu")
    (tmp_path / "doc" / "loose.djvu").symlink_to(tmp_path / "gone.djvu")
    assert place.adopt(loose, tmp_path / "doc").name == "loose_b.djvu"


@pytest.mark.parametrize(
    ("value", "want"), [(None, []), ("", []), ("paper.pdf", ["paper.pdf"]), (["a.pdf", "b.pdf"], ["a.pdf", "b.pdf"])]
)
def test_entries_reads_a_lone_string_as_one_entry_as_papis_does(value, want):
    doc = papis.document.from_data({} if value is None else {"files": value})
    assert place.entries(doc) == want
