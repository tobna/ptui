"""Queries, narrowing, sorting and display text."""

import papis.document
import pytest

from ptui import library

FIELDS = ["title", "author", "tags", "year"]


def docs(*data):
    """Build documents in memory.

    Args:
        *data: One dict of keys per document.

    Returns:
        The documents, in order.
    """
    return [papis.document.from_data(record) for record in data]


def test_alias_expansion():
    """`expand_aliases` expands known aliases at word starts and after `(`."""
    aliases = {"a": "author:", "t": "title:"}
    assert library.expand_aliases("a:Nauen t:vision x:y", aliases) == ("author:Nauen title:vision x:y")
    assert library.expand_aliases("(a:Nauen OR t:x) NOT ta:y", aliases) == "(author:Nauen OR title:x) NOT ta:y"


def test_scope_speaks_papis_query_grammar():
    """Scope speaks papis query grammar.

    The scope query goes to papis untouched, so its grammar (OR, NOT, groups,
    since 0.16) is ptui's too — with no parser here to keep in step.
    """

    def titles(query):
        """Run a scope query and read the titles.

        Args:
            query: The papis query.

        Returns:
            The titles, sorted.
        """
        return sorted(doc["title"] for doc in library.scope(query))

    assert titles("author:Vaswani OR author:He") == ["Attention Is All You Need", "Deep Residual Learning"]
    assert titles("NOT (author:Vaswani OR author:He)") == ["A Neural Probabilistic Language Model"]


def test_narrow_modes():
    """`narrow` works in fuzzy, substring and regex mode, and a bad regex finds nothing."""
    items = docs({"title": "Attention Is All You Need"}, {"title": "Deep Residual Learning"})
    assert len(library.narrow(items, "attn", FIELDS, "fuzzy")) == 1  # subsequence
    assert library.narrow(items, "attn", FIELDS, "substring") == []
    assert len(library.narrow(items, "residual", FIELDS, "substring")) == 1
    assert len(library.narrow(items, "^deep", FIELDS, "regex")) == 1
    assert library.narrow(items, "[", FIELDS, "regex") == []  # bad regex, no crash
    assert len(library.narrow(items, "  ", FIELDS, "fuzzy")) == 2


def test_dotted_resolve():
    """`resolve` walks dotted paths and gives `None` for a missing step."""
    doc = papis.document.from_data({"author_list": [{"family": "Vaswani"}], "year": 2017})
    assert library.resolve(doc, "author_list.0.family") == "Vaswani"
    assert library.resolve(doc, "author_list.9.family") is None
    assert library.resolve(doc, "nope.deeper") is None
    assert library.resolve(doc, "year") == 2017


def test_sort_puts_missing_keys_last_in_both_directions():
    """Sort puts missing keys last in both directions."""
    items = docs({"year": 2020, "title": "b"}, {"title": "a"}, {"year": "1999", "title": "c"})
    assert [doc["title"] for doc in library.sort(items, "year")] == ["c", "b", "a"]
    assert [doc["title"] for doc in library.sort(items, "year", reverse=True)] == ["b", "c", "a"]


def test_sort_tiebreak_is_stable():
    """Sort tiebreak is stable."""
    items = docs(
        {"year": 2020, "title": "zeta"},
        {"year": 2020, "title": "alpha"},
        {"year": 2019, "title": "beta"},
    )
    ordered = library.sort(items, "year", reverse=True, tiebreak="title")
    assert [doc["title"] for doc in ordered] == ["alpha", "zeta", "beta"]


def test_strip_latex_is_display_only():
    """Strip latex is display only."""
    assert library.strip_latex("{B}ERT: $\\ell_2$ norms") == "BERT: _2 norms"


def test_fit_counts_cells_not_characters():
    """Fit counts cells not characters."""
    from rich.cells import cell_len

    assert library.fit("short", 10) == "short"
    assert library.fit("Persönlichkeitsdiagnostik", 10) == "Persönlich…"[:9] + "…"
    assert cell_len(library.fit("Persönlichkeitsdiagnostik", 10)) == 10
    assert cell_len(library.fit("日本語のタイトル", 7)) == 7  # two cells per glyph
    assert library.fit("anything", 0) == ""


def test_fit_backs_off_to_a_word_boundary():
    """Fit backs off to a word boundary."""
    assert library.fit("Multi-Level Monte Carlo Gradient Descent", 34) == "Multi-Level Monte Carlo Gradient…"
    # a boundary that would throw away most of the budget is not worth taking
    assert library.fit("Persönlichkeitsdiagnostik unter", 12) == "Persönlichk…"
    # the colon wins over the space: a title's head is the informative part
    assert library.fit("Just Leaf It: Accelerating Diffusion Models", 20) == "Just Leaf It…"


def test_fit_lines_wraps_on_words_and_ellipsises_only_the_last_row():
    """Fit lines wraps on words and ellipsises only the last row."""
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
    """Display joins lists and flatten keeps author list indexable."""
    assert library.display(["a", "b"]) == "a, b"
    assert library.display(2017) == "2017"
    flat = library.flatten(docs({"tags": ["vision", "nlp"], "author_list": [{"family": "He"}]})[0])
    assert flat["tags"] == "vision, nlp"
    assert flat["author_list"][0]["family"] == "He"


def test_flatten_leaves_files_alone():
    """Regression.

    `files` was joined like any other list, so every `for entry in doc["files"]` walked the path
    one character at a time.
    """
    doc = docs({"files": ["../a.pdf", "b.pdf"], "tags": ["x", "y"]})[0]
    flat = library.flatten(doc)
    assert flat["files"] == ["../a.pdf", "b.pdf"]  # a list, still
    assert flat["files"][0] == "../a.pdf"  # a string would index to "."
    assert flat["tags"] == "x, y"  # ordinary lists are still joined


def test_parse_query_grammar():
    """`parse_query` handles aliases, negation, fields and open quotes."""
    assert library.parse_query("  ") == ()
    parsed = library.parse_query("vision -survey a:nauen", {"a": "author:"})
    assert [(term.text, term.field, term.negate) for term in parsed] == [
        ("vision", "", False),
        ("survey", "", True),
        ("nauen", "author", False),  # the alias, expanded
    ]
    # a quote you are still in the middle of typing must not raise
    assert [term.text for term in library.parse_query('"vision trans')] == ["vision trans"]
    assert [term.text for term in library.parse_query('"vision transformer" x')] == [
        "vision transformer",
        "x",
    ]
    # a URL is not a field-qualified term
    assert [(term.text, term.field) for term in library.parse_query("http://arxiv.org/abs/1")] == [
        ("http://arxiv.org/abs/1", "")
    ]
    assert [term.text for term in library.parse_query("-")] == ["-"]  # a lone dash is a term


def test_narrow_ands_its_terms_and_honours_fields_and_ranges():
    """Narrow ands its terms and honours fields and ranges."""
    FIELDS = ["title", "author", "year"]
    lib = docs(
        {"title": "Vision Transformer", "author": "Nauen", "year": 2024},
        {"title": "A Survey of Vision", "author": "Other", "year": 2019},
        {"title": "Efficient Attention", "author": "Nauen", "year": 2026},
    )

    def narrowed_titles(query, mode="substring"):
        """Narrow the test's documents and read what is left.

        Args:
            query: The narrow query.
            mode: The narrow mode.

        Returns:
            The titles left, in order.
        """
        return [doc["title"] for doc in library.narrow(lib, query, FIELDS, mode)]

    assert narrowed_titles("vision") == ["Vision Transformer", "A Survey of Vision"]
    assert narrowed_titles("vision nauen") == ["Vision Transformer"]  # ANDed
    assert narrowed_titles("nauen vision") == ["Vision Transformer"]  # order-independent
    assert narrowed_titles("vision -survey") == ["Vision Transformer"]  # negation
    assert narrowed_titles("author:nauen") == ["Vision Transformer", "Efficient Attention"]
    assert narrowed_titles("author:nauen -attention") == ["Vision Transformer"]
    assert narrowed_titles("year:>2023") == ["Vision Transformer", "Efficient Attention"]
    assert narrowed_titles("year:2019..2024") == ["Vision Transformer", "A Survey of Vision"]
    assert narrowed_titles("year:2024") == ["Vision Transformer"]  # a bare number is still a substring
    assert narrowed_titles('"vision transformer"') == ["Vision Transformer"]
    assert narrowed_titles("zzz") == []
    assert narrowed_titles("") == [doc["title"] for doc in lib]
    # fuzzy forgives a dropped letter inside a word, but stays tight — see
    # test_fuzzy_match_needs_the_run_to_stay_tight for what it now refuses
    assert narrowed_titles("vson", "fuzzy") == ["Vision Transformer", "A Survey of Vision"]
    assert narrowed_titles("efcient attntion", "fuzzy") == ["Efficient Attention"]
    assert narrowed_titles("zzz", "fuzzy") == []
    assert library.narrow(lib, "[", FIELDS, "regex") == []  # unparsable, not a crash


def test_fuzzy_match_needs_the_run_to_stay_tight():
    """Fuzzy match needs the run to stay tight."""
    assert library.fuzzy_match("note", "note")
    assert library.fuzzy_match("nte", "note")  # a dropped letter is fine
    assert not library.fuzzy_match("note", "locality-attending vision transformer")
    assert library.fuzzy_match("", "anything")


def test_venue_is_the_name_not_the_place():
    """Venue is the name not the place."""

    def venue_of(**fields):
        """Read the venue of a document with these keys.

        Args:
            **fields: The document's keys.

        Returns:
            The venue.
        """
        return library.venue(docs(fields)[0])

    assert venue_of(booktitle="NeurIPS") == "NeurIPS"
    assert venue_of(journal="TPAMI") == "TPAMI"
    assert venue_of(journaltitle="TMLR") == "TMLR"
    assert venue_of(booktitle="CVPR", journal="ignored") == "CVPR"  # preference order
    assert venue_of(journal="  ") == ""  # blank is not a name
    # the whole point: the `venue` key holds a city, and a city is not a venue name
    assert venue_of(venue="New Orleans, Louisiana, USA") == ""
    assert venue_of(venue="Sydney, Australia", booktitle="ICCV") == "ICCV"
    # and the flattened view a display path reads shows the name, never the city
    flat = library.flatten(docs({"venue": "Sydney, Australia", "booktitle": "ICCV"})[0])
    assert flat["venue"] == "ICCV"


def test_kind_calls_an_arxiv_only_article_a_preprint():
    """Kind calls an arxiv only article a preprint."""

    def kind_of(**fields):
        """Read the kind of a document with these keys.

        Args:
            **fields: The document's keys.

        Returns:
            The kind.
        """
        return library.kind(docs(fields)[0])

    arxiv = "10.48550/arXiv.2509.26092"
    assert kind_of(type="article", doi=arxiv) == "preprint"
    # no DOI *and* no arXiv trace at all: the 3 such documents in the real
    # library are an OpenAI blog post and an openreview draft, not preprints
    assert kind_of(type="article") == "article"
    assert kind_of(type="article", eprinttype="OpenAI Blog") == "article"
    assert kind_of(type="article", doi="10.1109/CVPR.2024.1") == "article"  # a publisher minted it
    assert kind_of(type="article", doi=arxiv, journal="TPAMI") == "article"
    assert kind_of(type="article", doi=arxiv, booktitle="NeurIPS") == "article"
    # `venue` holds the *city*, so it is not evidence of publication
    assert kind_of(type="article", doi=arxiv, venue="Vancouver, BC, Canada") == "preprint"
    assert kind_of(type="article", doi=arxiv, journaltitle="TMLR") == "article"
    assert kind_of(type="article", doi=arxiv, journal="  ") == "preprint"  # blank is not a venue
    assert kind_of(type="inproceedings", doi=arxiv) == "inproceedings"  # only articles can be preprints
    assert library.flatten(docs({"type": "article", "doi": arxiv})[0])["kind"] == "preprint"


def test_an_arxiv_entry_with_no_doi_is_still_a_preprint():
    """An arxiv entry with no doi is still a preprint.

    The LUMA case: imported from arXiv, never assigned a DOI at all. 19 such
    documents in the real library used to read as `article`.
    """

    def kind_of(**fields):
        """Read the kind of a document with these keys.

        Args:
            **fields: The document's keys.

        Returns:
            The kind.
        """
        return library.kind(docs(fields)[0])

    eprint = {"eprint": "2607.00687v1", "eprinttype": "arxiv"}
    assert kind_of(type="article", **eprint) == "preprint"
    assert kind_of(type="article", eprinttype="arXiv") == "preprint"  # case is not a signal
    assert kind_of(type="article", url="http://arxiv.org/abs/2607.00687v1") == "preprint"
    # …but a published paper keeps its arXiv eprint forever, and the DOI wins:
    # 182 documents in the real library carry both.
    assert kind_of(type="article", doi="10.1109/CVPR.2024.1", **eprint) == "article"
    assert kind_of(type="article", doi="10.1109/CVPR.2024.1", url="https://arxiv.org/abs/1") == "article"
    assert kind_of(type="article", journal="TPAMI", **eprint) == "article"  # so does a venue
    assert kind_of(type="article", eprinttype="openreview") == "article"  # another server is not arXiv


def test_p90_ignores_the_one_long_outlier():
    """P90 ignores the one long outlier."""
    assert library.percentile_90(["x" * 5] * 9 + ["x" * 40]) == 5
    assert library.percentile_90([]) == 0


def test_discover_keys():
    """`discover_keys` gives the sorted union of the documents' keys."""
    assert library.discover_keys(docs({"year": 1}, {"title": "x", "tags": ["a"]})) == [
        "tags",
        "title",
        "year",
    ]


def test_typed_follows_the_declared_type_then_the_stored_one():
    """Typed follows the declared type then the stored one."""
    import pytest

    assert library.typed("tags", "ml, cv") == ["ml", "cv"]  # papis declares tags:list
    assert library.typed("tags", " machine learning ,cv,") == ["machine learning", "cv"]  # commas only
    assert library.typed("year", "2020") == 2020  # ...and year:int
    assert library.typed("title", "2020") == "2020"  # a str key stays text
    # no declared type: the value already there decides, so a list stays a list
    assert library.typed("keywords", "a, b", current=["x"]) == ["a", "b"]
    assert library.typed("keywords", "a, b") == "a, b"
    assert library.typed("volume", "04", current="1") == "04"  # never guessed into a number
    assert library.typed("tags", "   ") is None  # nothing typed means remove the key
    with pytest.raises(ValueError):
        library.typed("year", "twenty")


@pytest.mark.parametrize(
    ("stored", "kind"),
    [("phdthesis", "thesis"), ("mastersthesis", "thesis"), ("techreport", "report"), ("conference", "inproceedings")],
)
def test_kind_reads_a_bibtex_alias_as_the_type_it_stands_for(stored, kind):
    """Kind reads a bibtex alias as the type it stands for.

    papis's own alias table, the one its `biblatex-type-alias` check reads:
    a `phdthesis` drew the unknown-kind glyph.

    Args:
        stored: Parametrized, such as `'phdthesis'`.
        kind: Parametrized, such as `'thesis'`.
    """
    assert library.kind(docs({"type": stored})[0]) == kind


@pytest.mark.parametrize(
    ("doi", "kind"),
    [
        ("https://doi.org/10.48550/arXiv.1706.03762", "preprint"),
        ("http://dx.doi.org/10.48550/ARXIV.1706.03762", "preprint"),
        ("doi:10.48550/arXiv.1706.03762", "preprint"),
        ("https://doi.org/10.1145/3065386", "article"),  # a publisher's, whatever the arXiv trace
    ],
)
def test_kind_reads_a_doi_url_as_the_bare_doi(doi, kind):
    """Kind reads a doi url as the bare doi.

    Args:
        doi: Parametrized, such as `'https://doi.org/10.48550/arXiv.1706.03762'`.
        kind: Parametrized, such as `'preprint'`.
    """
    assert library.kind(docs({"type": "article", "doi": doi, "eprinttype": "arxiv"})[0]) == kind


@pytest.mark.parametrize(
    ("query", "mode"),
    [
        ("bert", "substring"),
        ("title:bert", "substring"),
        ("{b}ert", "substring"),
        ("bert", "fuzzy"),
        ("^bert", "regex"),
    ],
)
def test_the_narrow_finds_a_title_as_shown_and_as_stored(query, mode):
    """The narrow finds a title as shown and as stored.

    Args:
        query: Parametrized, such as `'bert'`.
        mode: Parametrized, such as `'substring'`.
    """
    shown = docs({"title": "{B}ERT: Pre-training"})
    assert library.narrow(shown, query, ["title"], mode) == shown
    if mode != "regex":  # a regex has no negation
        assert library.narrow(shown, f"-{query}", ["title"], mode) == []


def test_kind_ignores_the_case_of_the_type():
    """Kind ignores the case of the type."""
    assert library.kind(docs({"type": "Article", "doi": "10.48550/arXiv.1"})[0]) == "preprint"
