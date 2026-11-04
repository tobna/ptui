"""Metadata importers, with the network stubbed out."""

from pathlib import Path

import pytest

from ptui import config, fetch

BIB = (
    "@inproceedings{he2016deep, title={Deep Residual Learning}, "
    "author={He, Kaiming and Zhang, Xiangyu}, year={2016}, booktitle={CVPR}}\n"
    "@article{jmlr2022, title={Benchmarking GNNs}, author={Dwivedi, Vijay}, "
    "year={2022}, journal={JMLR}}\n"
)


def test_sources_are_curated_and_available_is_a_subset(monkeypatch):
    """Sources are curated and available is a subset.

    `fallback` and `get` must never appear: they exist for URL dispatch and
    would try to HTTP-GET whatever the user typed.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
    """
    shipped = config.load(config.DEFAULTS_DIR / "nonexistent.toml").get("add.sources")
    assert "fallback" not in shipped
    assert "get" not in shipped
    # the rows and their order from before they moved into config.toml
    assert list(shipped) == [
        *("arxiv", "doi", "isbn", "pmid", "dblp", "zenodo", "crossref"),
        *("bibtex", "yaml", "folder", "lib", "pdf2doi", "pdf2arxivid"),
    ]
    assert set(fetch.available(shipped)) <= set(shipped)
    assert "arxiv" in fetch.available(shipped)
    assert "doi" in fetch.available(shipped)
    # an importer papis drops is skipped, not crashed on
    monkeypatch.setattr("papis.importer.get_available_importers", lambda: ["arxiv"])
    assert fetch.available(shipped) == ["arxiv"]


def test_bib_entries_returns_every_entry(tmp_path):
    """Bib entries returns every entry.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    path = tmp_path / "refs.bib"
    path.write_text(BIB)
    entries = fetch.bib_entries(path)
    assert [entry["title"] for entry in entries] == ["Deep Residual Learning", "Benchmarking GNNs"]
    assert entries[0]["ref"] == "he2016deep"
    assert entries[0]["booktitle"] == "CVPR"


def test_bib_entries_rejects_an_empty_file(tmp_path):
    """Bib entries rejects an empty file.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    path = tmp_path / "empty.bib"
    path.write_text("% nothing here\n")
    with pytest.raises(ValueError, match="no entries"):
        fetch.bib_entries(path)


def test_from_url_refuses_anything_that_is_not_http(tmp_path):
    """The guard that matters.

    `fallback` matches any string, so a bare path would be handed to urllib and the `doi`
    importer's match() would GET doi.org.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    for bad in (str(tmp_path / "x.pdf"), "/etc/passwd", "ftp://host/x", "10.1109/x"):
        with pytest.raises(ValueError, match="http"):
            fetch.from_url(bad)


def test_fetch_reports_a_uri_the_importer_will_not_take():
    """Fetch reports a uri the importer will not take."""
    with pytest.raises(ValueError, match="arxiv could not import 'definitely not an arxiv id'"):
        fetch.fetch("arxiv", "definitely not an arxiv id")


def test_crossref_tells_a_failed_request_from_an_empty_answer(monkeypatch):
    """Crossref tells a failed request from an empty answer.

    papis returns [] for both and only logs the failure: offline, `c u` and
    the `crossref` add source said the paper did not exist.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
    """

    def offline(**_):
        """Fail as a request does with no network.

        Args:
            **_: The request's arguments; unused.

        Raises:
            OSError: Always.
        """
        raise OSError("Name or service not known")

    monkeypatch.setattr("papis.crossref._get_crossref_works", offline)
    with pytest.raises(ConnectionError, match="could not be reached"):
        fetch.crossref("attention")
    monkeypatch.setattr("papis.crossref._get_crossref_works", lambda **_: {"message": {"items": []}})
    with pytest.raises(ValueError, match="found nothing"):
        fetch.crossref("attention")


def test_fetch_maps_files_to_paths(monkeypatch):
    """Fetch maps files to paths.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
    """

    class FakeCtx:
        """A stand-in for an importer's context."""

        def __init__(self):
            """Hold one record and one file."""
            self.data = {"title": "T", "year": "2024"}
            self.files = ["/tmp/a.pdf"]

    class FakeImporter:
        """A stand-in for an importer."""

        def __init__(self):
            """Hold a fake context."""
            self.ctx = FakeCtx()

        def fetch(self):
            """Do nothing: the context is already filled."""
            pass

    class FakeCls:
        """A stand-in for an importer class."""

        @staticmethod
        def match(uri):
            """Accept any URI.

            Args:
                uri: What to import; unused.

            Returns:
                A fake importer.
            """
            return FakeImporter()

    monkeypatch.setattr("papis.importer.get_importer_by_name", lambda name: FakeCls)
    data, files = fetch.fetch("arxiv", "2509.26092")
    assert data["title"] == "T"
    assert files == [Path("/tmp/a.pdf")]
