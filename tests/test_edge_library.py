"""Edges of the narrow grammar, sorting, `kind()` and cell-accurate fitting."""

import papis.document
import pytest
from rich.cells import cell_len

from ptui import library

FIELDS = ["title", "author", "tags", "year"]
ALIASES = {"a": "author:", "t": "title:"}  # the shipped shape: the colon is part of the alias


def _titles(data, query, mode="substring", aliases=None):
    docs = [papis.document.from_data(d) for d in data]
    return [d["title"] for d in library.narrow(docs, query, FIELDS, mode, aliases)]


YEARS = [
    {"title": "old", "year": 2003},
    {"title": "mid", "year": "2016"},
    {"title": "new", "year": 2017},
    {"title": "none"},
]


@pytest.mark.parametrize(
    ("query", "want"),
    [
        ("year:>2016", ["new"]),
        ("year:>=2016", ["mid", "new"]),
        ("year:<2016", ["old"]),
        ("year:<=2016", ["old", "mid"]),
        ("year:2004..2017", ["mid", "new"]),
        ("year:..2016", ["old", "mid"]),
        ("year:2016..", ["mid", "new"]),
        ("year:2016", ["mid"]),  # no operator: a substring, not a range
        ("-year:>2016", ["old", "mid", "none"]),  # no year is not "after 2016"
        ("year:>", ["old", "mid", "new"]),  # half-typed: every dated document, nothing crashes
    ],
)
def test_ranges(query, want):
    assert _titles(YEARS, query) == want


@pytest.mark.parametrize(
    ("query", "terms"),
    [
        ('-"all you"', [library.Term("all you", "", True)]),
        ('"all yo', [library.Term("all yo")]),  # the half-typed quote is closed for you
        ("http://x.org", [library.Term("http://x.org")]),  # a URL is not the field `http`
        ("- -x", [library.Term("-"), library.Term("x", "", True)]),  # a lone dash is text
        ("title: -title:", []),  # a field with nothing after it narrows nothing yet
        ("doi:10.1/a:b", [library.Term("10.1/a:b", "doi")]),
    ],
)
def test_parse_query_edges(query, terms):
    assert list(library.parse_query(query)) == terms


APOSTROPHES = [{"title": "Don't Stop Pretraining", "author": "Gururangan"}, {"title": "Other", "author": "O'Brien"}]


@pytest.mark.parametrize(("query", "want"), [("don't", ["Don't Stop Pretraining"]), ("o'brien", ["Other"])])
def test_an_apostrophe_is_part_of_the_term(query, want):
    assert _titles(APOSTROPHES, query) == want


def test_a_negated_alias_excludes():
    data = [{"title": "mine", "author": "Nauen"}, {"title": "theirs", "author": "Vaswani"}]
    assert _titles(data, "a:nauen", aliases=ALIASES) == ["mine"]
    assert _titles(data, "-a:nauen", aliases=ALIASES) == ["theirs"]


@pytest.mark.xfail(
    strict=True,
    reason=r"BUG: regex mode casefolds the pattern, which turns `\D`/`\W`/`\S` into `\d`/`\w`/`\s`",
)
def test_regex_mode_keeps_uppercase_escapes():
    data = [{"title": "Attention"}, {"title": "1984"}]
    assert _titles(data, r"^\D", mode="regex") == ["Attention"]


def test_regex_mode_is_case_insensitive():
    assert _titles([{"title": "Attention"}], "ATTENTION", mode="regex") == ["Attention"]


def test_resolve_negative_index_and_dead_ends():
    doc = papis.document.from_data({"author_list": [{"family": "A"}, {"family": "B"}], "title": "T"})
    assert library.resolve(doc, "author_list.-1.family") == "B"
    assert library.resolve(doc, "author_list.2.family") is None
    assert library.resolve(doc, "title.0") is None  # a string is not indexed
    assert library.resolve(doc, "author_list.x") is None


def test_sort_numeric_text_numerically_and_numbers_before_words():
    docs = [papis.document.from_data({"v": v} if v is not None else {}) for v in ["10", "9", 2, "b", "A", None]]
    assert [d.get("v") for d in library.sort(docs, "v")] == [2, "9", "10", "A", "b", None]
    assert library.sort(docs, "v", null_ordering="first")[0].get("v") is None


@pytest.mark.parametrize(
    ("data", "want"),
    [
        ({"type": "article", "doi": "10.48550/ARXIV.1706.03762"}, "preprint"),  # the prefix, whatever the case
        ({"type": "article", "venue": "New Orleans, USA", "eprinttype": "arXiv"}, "preprint"),  # a city is no venue
        ({"type": "article", "eprinttype": "openreview"}, "article"),
        ({"type": "article", "journal": " ", "url": "https://arxiv.org/abs/1"}, "preprint"),  # blank journal
        ({}, ""),
    ],
)
def test_kind_edges(data, want):
    assert library.kind(papis.document.from_data(data)) == want


@pytest.mark.parametrize(
    "text",
    [
        "日本語のタイトルはとても長いので切られる必要があります",
        "Attention 日本語 is all you need ✨ really",
        "A supercalifragilisticexpialidocious word in the middle",
        "é combining marks count as zero cells but still wrap",
    ],
)
@pytest.mark.parametrize(("width", "lines"), [(1, 1), (2, 2), (5, 3), (9, 2), (12, 1)])
def test_fit_lines_never_overflows_a_cell(text, width, lines):
    rows = library.fit_lines(text, width, lines).split("\n")
    assert len(rows) <= lines
    assert all(cell_len(row) <= width for row in rows)
