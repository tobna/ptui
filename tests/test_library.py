import papis.document

from ptui import library

FIELDS = ["title", "author", "tags", "year"]


def docs(*data):
    return [papis.document.from_data(d) for d in data]


def test_alias_expansion():
    aliases = {"a": "author:", "t": "title:"}
    assert library.expand_aliases("a:Nauen t:vision x:y", aliases) == (
        "author:Nauen title:vision x:y"
    )


def test_narrow_modes():
    items = docs({"title": "Attention Is All You Need"}, {"title": "Deep Residual Learning"})
    assert len(library.narrow(items, "attn", FIELDS, "fuzzy")) == 1  # subsequence
    assert library.narrow(items, "attn", FIELDS, "substring") == []
    assert len(library.narrow(items, "residual", FIELDS, "substring")) == 1
    assert len(library.narrow(items, "^deep", FIELDS, "regex")) == 1
    assert library.narrow(items, "[", FIELDS, "regex") == []  # bad regex, no crash
    assert len(library.narrow(items, "  ", FIELDS, "fuzzy")) == 2


def test_dotted_resolve():
    doc = papis.document.from_data({"author_list": [{"family": "Vaswani"}], "year": 2017})
    assert library.resolve(doc, "author_list.0.family") == "Vaswani"
    assert library.resolve(doc, "author_list.9.family") is None
    assert library.resolve(doc, "nope.deeper") is None
    assert library.resolve(doc, "year") == 2017


def test_sort_puts_missing_keys_last_in_both_directions():
    items = docs({"year": 2020, "title": "b"}, {"title": "a"}, {"year": "1999", "title": "c"})
    assert [d["title"] for d in library.sort(items, "year")] == ["c", "b", "a"]
    assert [d["title"] for d in library.sort(items, "year", reverse=True)] == ["b", "c", "a"]


def test_sort_tiebreak_is_stable():
    items = docs(
        {"year": 2020, "title": "zeta"},
        {"year": 2020, "title": "alpha"},
        {"year": 2019, "title": "beta"},
    )
    ordered = library.sort(items, "year", reverse=True, tiebreak="title")
    assert [d["title"] for d in ordered] == ["alpha", "zeta", "beta"]


def test_strip_latex_is_display_only():
    assert library.strip_latex("{B}ERT: $\\ell_2$ norms") == "BERT: _2 norms"


def test_fit_counts_cells_not_characters():
    from rich.cells import cell_len

    assert library.fit("short", 10) == "short"
    assert library.fit("Persönlichkeitsdiagnostik", 10) == "Persönlich…"[:9] + "…"
    assert cell_len(library.fit("Persönlichkeitsdiagnostik", 10)) == 10
    assert cell_len(library.fit("日本語のタイトル", 7)) == 7  # two cells per glyph
    assert library.fit("anything", 0) == ""


def test_discover_keys():
    assert library.discover_keys(docs({"year": 1}, {"title": "x", "tags": []})) == [
        "tags",
        "title",
        "year",
    ]
