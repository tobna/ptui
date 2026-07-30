"""Metadata from an arXiv id, a DOI, or a `.bib` file — plus the arXiv PDF.

All three fetchers are papis's own (`papis.arxiv`, `papis.crossref`,
`papis.bibtex`); nothing here parses a response. What it adds is deciding *which*
fetcher an arbitrary string wants, and downloading the arXiv PDF, which papis
exposes as a URL but does not retrieve.

Everything here touches the network except `from_bibtex` and `guess`. Callers run
them off the UI thread.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Any

USER_AGENT = "ptui (+https://github.com/papis/papis)"
"""arXiv asks for a real agent string and throttles the default one."""

_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)


def guess(text: str) -> str:
    """Which source a pasted string is: `arxiv`, `doi`, `bib`, or `""`.

    Order matters. An arXiv DOI (`10.48550/arXiv.2509.26092`) is both, and the
    arXiv fetcher gives more back — including the PDF — so it wins.
    """
    import papis.arxiv

    text = text.strip()
    if not text:
        return ""
    if text.lower().endswith(".bib") or Path(text).expanduser().suffix == ".bib":
        return "bib"
    if papis.arxiv.is_arxivid(text) or papis.arxiv.find_arxivid_in_text(text):
        return "arxiv"
    return "doi" if _DOI.search(text) else ""


def arxiv_id(text: str) -> str:
    """The bare id out of `2509.26092`, `arXiv:2509.26092` or an abs/pdf URL."""
    import papis.arxiv

    text = text.strip()
    found = papis.arxiv.find_arxivid_in_text(text)
    if found:
        return found
    return text if papis.arxiv.is_arxivid(text) else ""


def from_arxiv(text: str) -> dict[str, Any]:
    import papis.arxiv

    identifier = arxiv_id(text)
    if not identifier:
        raise ValueError(f"not an arXiv id: {text!r}")
    results = papis.arxiv.get_data(id_list=identifier, max_results=1)
    if not results:
        raise ValueError(f"arXiv returned nothing for {identifier!r}")
    return dict(results[0])


def from_doi(text: str) -> dict[str, Any]:
    import papis.crossref

    found = _DOI.search(text.strip())
    doi = found.group(1) if found else text.strip()
    data = papis.crossref.doi_to_data(doi)
    if not data:
        raise ValueError(f"crossref returned nothing for {doi!r}")
    return dict(data)


def from_bibtex(path: Path) -> list[dict[str, Any]]:
    import papis.bibtex

    entries = papis.bibtex.bibtex_to_dict(path.expanduser().read_text(encoding="utf-8"))
    if not entries:
        raise ValueError(f"no entries in {path}")
    return [dict(entry) for entry in entries]


def download(url: str, dest: Path) -> Path:
    """Fetch `url` to `dest`, refusing anything that is not a PDF.

    The check is the file's own magic, not the content type: arXiv has served
    HTML error pages with a PDF content type, and a 12-byte "PDF" that papis then
    files away as the paper is worse than a failed add.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        blob = response.read()
    if not blob.startswith(b"%PDF"):
        raise ValueError(f"{url} did not return a PDF")
    dest.write_bytes(blob)
    return dest


def arxiv_pdf(data: dict[str, Any], into: Path) -> Path | None:
    """Download the PDF an arXiv record points at. `None` when there is no URL."""
    url = str(data.get("doc_url") or "")
    if not url.startswith("https://"):
        return None
    name = f"{arxiv_id(str(data.get('eprint') or '')) or 'arxiv'}.pdf".replace("/", "_")
    return download(url, into / name)
