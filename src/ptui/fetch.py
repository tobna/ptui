"""Metadata from wherever papis can already get it.

papis ships an importer plugin system — 13 importers and 23 downloaders here —
and this module is a thin front for it. Nothing parses a response, nothing
downloads a PDF: `Importer.fetch()` already fills `ctx.data` and, for arXiv and
the publisher downloaders, `ctx.files` with a PDF it retrieved itself.

Two traps, both found by measurement and both the reason this does not simply
call `papis.importer.get_matching_importers_by_uri`:

* **Matching hits the network.** The `doi` importer's `match()` calls
  `validate_doi`, which HTTP-GETs doi.org — for *any* string, including a local
  file path — and raises `InvalidURL` rather than declining. So the importer is
  chosen by name from what the user picked, never by asking all of them.
* **The `fallback` downloader will try to GET a filesystem path.** URL dispatch
  is therefore restricted to `http://` and `https://`.

Everything here except `available` and `bib_entries` touches the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SOURCES: dict[str, tuple[str, str]] = {
    # importer name: (what to call it, what to ask for)
    "arxiv": ("an arXiv id or URL", "2509.26092, arXiv:2509.26092 or an abs/pdf URL"),
    "doi": ("a DOI", "10.1109/CVPR52688.2022.01167"),
    "isbn": ("an ISBN", "978-0262035613"),
    "pmid": ("a PubMed id", "31452104"),
    "dblp": ("a DBLP key or URL", "conf/cvpr/HeZRS16"),
    "zenodo": ("a Zenodo id or URL", "record id or URL"),
    "crossref": ("a Crossref search", "title, author, anything"),
    "bibtex": ("a .bib file", "path to a .bib"),
    "yaml": ("a papis .yaml file", "path to an info.yaml"),
    "folder": ("an existing papis folder", "path to a document folder"),
    "lib": ("another papis library", "a query in that library"),
    "pdf2doi": ("a PDF, read its DOI", "path to a PDF"),
    "pdf2arxivid": ("a PDF, read its arXiv id", "path to a PDF"),
}
"""Importers worth offering, in the order they should appear.

Deliberately a curated table rather than the raw plugin list: `fallback` and
`get` exist to serve URL dispatch and mean nothing as a menu entry, and an
importer nobody can describe is not a source a user can choose. An importer papis
does not have is skipped, so this degrades rather than breaking.
"""


def available() -> list[str]:
    """The `SOURCES` papis actually registers, so a new plugin appears for free."""
    import papis.importer

    known = set(papis.importer.get_available_importers())
    return [name for name in SOURCES if name in known]


def fetch(name: str, uri: str) -> tuple[dict[str, Any], list[Path]]:
    """Run one named importer. Returns its metadata and any file it downloaded.

    `match()` is what constructs the importer — constructors differ per plugin
    (`ArxivImporter` wants `arxivid`, not `uri`), so it is the only generic way in.
    """
    import papis.importer

    importer = papis.importer.get_importer_by_name(name).match(uri.strip())
    if importer is None:
        raise ValueError(f"{SOURCES.get(name, (name,))[0]} does not match {uri!r}")
    importer.fetch()
    data = dict(importer.ctx.data or {})
    if not data:
        raise ValueError(f"{name} found nothing for {uri!r}")
    return data, [Path(f) for f in importer.ctx.files or []]


def from_url(url: str) -> tuple[dict[str, Any], list[Path]]:
    """Let papis pick a downloader for a publisher URL — 23 of them ship with it.

    `http(s)` only: `fallback` matches anything and would try to GET a path.
    """
    import papis.importer

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("a URL has to start with http:// or https://")
    matched = papis.importer.get_matching_importers_by_uri(url, include_downloaders=True)
    errors = []
    for importer in matched:
        try:
            importer.fetch()
        except Exception as exc:  # a downloader that cannot parse this page
            errors.append(f"{importer.name}: {exc}")
            continue
        if importer.ctx.data:
            return dict(importer.ctx.data), [Path(f) for f in importer.ctx.files or []]
    detail = "; ".join(errors[:3]) or "no importer matched"
    raise ValueError(f"nothing could read {url} ({detail})")


def bib_entries(path: Path) -> list[dict[str, Any]]:
    """Every entry in a `.bib`, so the user can choose which one to add.

    The `bibtex` importer returns only the first entry in `ctx.data`; this is the
    same parser underneath, kept whole.
    """
    import papis.bibtex

    entries = papis.bibtex.bibtex_to_dict(path.expanduser().read_text(encoding="utf-8"))
    if not entries:
        raise ValueError(f"no entries in {path}")
    return [dict(entry) for entry in entries]
