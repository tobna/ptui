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


def test_fit_backs_off_to_a_word_boundary():
    assert (
        library.fit("Multi-Level Monte Carlo Gradient Descent", 34)
        == "Multi-Level Monte Carlo Gradient…"
    )
    # a boundary that would throw away most of the budget is not worth taking
    assert library.fit("Persönlichkeitsdiagnostik unter", 12) == "Persönlichk…"
    # the colon wins over the space: a title's head is the informative part
    assert library.fit("Just Leaf It: Accelerating Diffusion Models", 20) == "Just Leaf It…"


def test_fit_lines_wraps_on_words_and_ellipsises_only_the_last_row():
    from rich.cells import cell_len

    title = "Multilevel Stochastic Gradient Descent for Neural Networks"
    assert library.fit_lines(title, 20, 1) == library.fit(title, 20)  # one line is just fit
    rows = library.fit_lines(title, 20, 3).split("\n")
    assert rows == ["Multilevel", "Stochastic Gradient", "Descent for Neural…"]
    assert all(cell_len(row) <= 20 for row in rows)
    assert "\n" not in library.fit_lines("short", 20, 2)  # no padding to the full height
    # a budget of width * lines would have kept text that cannot actually wrap in
    assert library.fit_lines(title, 20, 2).endswith("…")
    # a single word wider than the column is cut rather than split mid-word
    assert library.fit_lines("Persönlichkeitsdiagnostik", 10, 2) == "Persönlic…"


def test_display_joins_lists_and_flatten_keeps_author_list_indexable():
    assert library.display(["a", "b"]) == "a, b"
    assert library.display(2017) == "2017"
    flat = library.flatten(docs({"tags": ["vision", "nlp"], "author_list": [{"family": "He"}]})[0])
    assert flat["tags"] == "vision, nlp"
    assert flat["author_list"][0]["family"] == "He"


def test_parse_query_grammar():
    assert library.parse_query("  ") == ()
    parsed = library.parse_query("vision -survey a:nauen", {"a": "author:"})
    assert [(t.text, t.field, t.negate) for t in parsed] == [
        ("vision", "", False),
        ("survey", "", True),
        ("nauen", "author", False),  # the alias, expanded
    ]
    # a quote you are still in the middle of typing must not raise
    assert [t.text for t in library.parse_query('"vision trans')] == ["vision trans"]
    assert [t.text for t in library.parse_query('"vision transformer" x')] == [
        "vision transformer",
        "x",
    ]
    # a URL is not a field-qualified term
    assert [(t.text, t.field) for t in library.parse_query("http://arxiv.org/abs/1")] == [
        ("http://arxiv.org/abs/1", "")
    ]
    assert [t.text for t in library.parse_query("-")] == ["-"]  # a lone dash is a term


def test_narrow_ands_its_terms_and_honours_fields_and_ranges():
    FIELDS = ["title", "author", "year"]
    lib = docs(
        {"title": "Vision Transformer", "author": "Nauen", "year": 2024},
        {"title": "A Survey of Vision", "author": "Other", "year": 2019},
        {"title": "Efficient Attention", "author": "Nauen", "year": 2026},
    )

    def n(query, mode="substring"):
        return [d["title"] for d in library.narrow(lib, query, FIELDS, mode)]

    assert n("vision") == ["Vision Transformer", "A Survey of Vision"]
    assert n("vision nauen") == ["Vision Transformer"]  # ANDed
    assert n("nauen vision") == ["Vision Transformer"]  # order-independent
    assert n("vision -survey") == ["Vision Transformer"]  # negation
    assert n("author:nauen") == ["Vision Transformer", "Efficient Attention"]
    assert n("author:nauen -attention") == ["Vision Transformer"]
    assert n("year:>2023") == ["Vision Transformer", "Efficient Attention"]
    assert n("year:2019..2024") == ["Vision Transformer", "A Survey of Vision"]
    assert n("year:2024") == ["Vision Transformer"]  # a bare number is still a substring
    assert n('"vision transformer"') == ["Vision Transformer"]
    assert n("zzz") == []
    assert n("") == [d["title"] for d in lib]
    # fuzzy forgives a dropped letter inside a word, but stays tight — see
    # test_fuzzy_match_needs_the_run_to_stay_tight for what it now refuses
    assert n("vson", "fuzzy") == ["Vision Transformer", "A Survey of Vision"]
    assert n("efcient attntion", "fuzzy") == ["Efficient Attention"]
    assert n("zzz", "fuzzy") == []
    assert library.narrow(lib, "[", FIELDS, "regex") == []  # unparsable, not a crash


def test_fuzzy_match_needs_the_run_to_stay_tight():
    assert library.fuzzy_match("note", "note")
    assert library.fuzzy_match("nte", "note")  # a dropped letter is fine
    assert not library.fuzzy_match("note", "locality-attending vision transformer")
    assert library.fuzzy_match("", "anything")


def test_kind_calls_an_arxiv_only_article_a_preprint():
    def k(**fields):
        return library.kind(docs(fields)[0])

    arxiv = "10.48550/arXiv.2509.26092"
    assert k(type="article", doi=arxiv) == "preprint"
    # an arXiv DOI is required, not merely the absence of a publisher one: 25 docs
    # in the real library have no DOI at all and stay `article` because of this
    assert k(type="article") == "article"
    assert k(type="article", doi="10.1109/CVPR.2024.1") == "article"  # a publisher minted it
    assert k(type="article", doi=arxiv, journal="TPAMI") == "article"
    assert k(type="article", doi=arxiv, booktitle="NeurIPS") == "article"
    assert k(type="article", doi=arxiv, venue="ICML") == "article"
    assert k(type="article", doi=arxiv, journal="  ") == "preprint"  # blank is not a venue
    assert k(type="inproceedings", doi=arxiv) == "inproceedings"  # only articles can be preprints
    assert library.flatten(docs({"type": "article", "doi": arxiv})[0])["kind"] == "preprint"


def test_p90_ignores_the_one_long_outlier():
    assert library.p90(["x" * 5] * 9 + ["x" * 40]) == 5
    assert library.p90([]) == 0


def test_discover_keys():
    assert library.discover_keys(docs({"year": 1}, {"title": "x", "tags": []})) == [
        "tags",
        "title",
        "year",
    ]
