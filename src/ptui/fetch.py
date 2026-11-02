"""Metadata from wherever papis can already get it.

papis ships an importer plugin system (`available()`, `downloaders()`) and
this module is a thin front for it. Nothing parses a response, nothing
downloads a PDF: `Importer.fetch()` already fills `ctx.data` and, for arXiv and
the publisher downloaders, `ctx.files` with a PDF it retrieved itself.

Two traps, both found by measurement and both the reason this does not simply
call `papis.importer.get_matching_importers_by_uri`:

* **Matching hits the network.** The `doi` importer's `match()` calls
  `validate_doi`, which HTTP-GETs doi.org for any string that is not an existing
  path (papis 0.16 stopped it for paths, papis#1201). So the importer is chosen
  by name from what the user picked, never by asking all of them.
* **The `fallback` downloader will try to GET a filesystem path.** URL dispatch
  is therefore restricted to `http://` and `https://`.

Everything here except `available`, `downloaders`, `refetchers` and
`bib_entries` touches the network.
"""

from __future__ import annotations

import logging.handlers
import threading
from pathlib import Path
from typing import Any


def available(sources: dict[str, dict[str, str]]) -> list[str]:
    """The `[add.sources]` papis actually registers, in their order.

    A curated table rather than the raw plugin list: an importer nobody has
    described is not a source a user can choose. A missing plugin is skipped,
    so this degrades rather than breaking; a new one is offered once the
    config gives it a row."""
    import papis.importer

    known = set(papis.importer.get_available_importers())
    return [name for name in sources if name in known]


def downloaders() -> list[str]:
    """Every downloader papis registers — what `from_url` can dispatch to."""
    import papis.downloaders

    return papis.downloaders.get_available_downloaders()


def fetch(name: str, uri: str) -> tuple[dict[str, Any], list[Path]]:
    """Run one named importer. Returns its metadata and any file it downloaded.

    `match()` is what constructs the importer — constructors differ per plugin
    (`ArxivImporter` wants `arxivid`, not `uri`), so it is the only generic way in.
    """
    import papis.importer

    importer = papis.importer.get_importer_by_name(name).match(uri.strip())
    if importer is None:
        raise ValueError(f"{name} does not match {uri!r}")
    importer.fetch()
    data = dict(importer.ctx.data or {})
    if not data:
        raise ValueError(f"{name} found nothing for {uri!r}")
    return data, [Path(f) for f in importer.ctx.files or []]


def from_url(url: str) -> tuple[dict[str, Any], list[Path]]:
    """Let papis pick one of its `downloaders()` for a publisher URL.

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


def refetchers(doc: dict[str, Any]) -> list[Any]:
    """The importers that can fetch `doc` again, built from its own data with no
    network: papis's data matching (the DOI; the title, for Crossref), plus arXiv
    for an arXiv id — that importer matches no data of its own."""
    import papis.importer

    found = papis.importer.get_matching_importers_by_doc(doc)
    eprint = doc.get("eprint") if str(doc.get("eprinttype") or "arxiv").casefold() == "arxiv" else None
    for hint in (doc.get("arxivid"), eprint, doc.get("url")):
        arxiv = papis.importer.get_importer_by_name("arxiv").match(str(hint)) if hint else None
        if arxiv is not None:
            found.append(arxiv)
            break
    order = ["doi", "arxiv", "crossref"]  # authority first; a title search last
    return sorted(found, key=lambda imp: order.index(imp.name) if imp.name in order else len(order))


CROSSREF_HITS = 10
"""How many hits a Crossref search lists; Crossref's own page is 20."""


def crossref(query: str) -> list[dict[str, Any]]:
    """Every hit of a Crossref search, best first. papis's `crossref` importer
    keeps only the first ("Picking the first one!") and its `match()` accepts no
    text at all, so neither `c u` nor the add flow could choose. Network.

    A failed request also returns `[]`: papis catches it and logs an error, and
    that record is the one thing telling it from an answer with no hits."""
    import papis.crossref

    failed = logging.handlers.BufferingHandler(capacity=8)  # keeps what it hears in `.buffer`
    failed.setLevel(logging.ERROR)
    failed.addFilter(lambda record: record.thread == threading.get_ident())  # not another worker's request
    papis.crossref.logger.addHandler(failed)
    try:
        hits = papis.crossref.get_data(query=query.strip(), max_results=CROSSREF_HITS)
    finally:
        papis.crossref.logger.removeHandler(failed)
    if failed.buffer:
        # ponytail: at `log.level = "critical"` papis drops the record before any
        # handler sees it, so there a failure still reads as no hits
        raise ConnectionError("Crossref could not be reached; see the papis log above")
    if not hits:
        raise ValueError(f"Crossref found nothing for {query!r}")
    return [dict(hit) for hit in hits]


def refetch(importer: Any) -> list[dict[str, Any]]:
    """Metadata only, never the PDF — the document already has its files. One
    record for an id; every hit for a Crossref title search. Network."""
    import papis.importer

    if importer.name == "crossref":
        found = crossref(importer.uri)
    else:
        done = papis.importer.fetch_importers([importer], download_files=False)
        found = [dict(done[0].ctx.data)] if done and done[0].ctx.data else []
    if not found:
        raise ValueError(f"{importer.name} found nothing for {importer.uri}")
    # the ref is the library's own; citations live in papis's citations file
    for data in found:
        for key in ("ref", "citations", "files"):
            data.pop(key, None)
    return found


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
