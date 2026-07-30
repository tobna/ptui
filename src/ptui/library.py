"""The two query layers, sorting, and display text.

SPEC keeps these strictly apart and so does this module:

* **scope** — a real papis query, run against the database on submit. What it
  supports depends on the papis backend.
* **narrow** — an in-memory filter over the scoped set. Backend-independent,
  identical on every machine.

Sorting applies to the scoped set and is independent of narrowing.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any

from rich.cells import cell_len, set_cell_size

# Papis forks a process pool to build its cache. Inside a TUI that copies the
# whole app and dies on Textual's redirected file descriptors, so ask papis for
# the sequential path (PAPIS_NP is papis's own switch — a user can override it).
os.environ.setdefault("PAPIS_NP", "0")

import papis.database
import papis.id
from papis.document import Document

ID_KEY = papis.id.ID_KEY_NAME


def doc_id(doc: Document) -> str:
    """The stable identity of a document. Never use the folder path — `papis rename` moves it."""
    return str(doc.get(ID_KEY, doc.get_main_folder() or id(doc)))


def expand_aliases(query: str, aliases: dict[str, str]) -> str:
    """`a:Nauen` -> `author:Nauen`, per `[query.aliases]`."""
    words = []
    for word in query.split():
        prefix, sep, rest = word.partition(":")
        words.append(aliases[prefix] + rest if sep and prefix in aliases else word)
    return " ".join(words)


def scope(query: str, library: str | None = None) -> list[Document]:
    db = papis.database.get(library or None)
    return db.query(query) if query.strip() else db.get_all_documents()


# ── narrow ──────────────────────────────────────────────────────────────────


def haystack(doc: Document, fields: list[str]) -> str:
    return " ".join(str(doc.get(f, "")) for f in fields).casefold()


def is_subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(char in it for char in needle)


def narrow(docs: list[Document], query: str, fields: list[str], mode: str) -> list[Document]:
    """Filter in memory. Unparsable regex matches nothing rather than raising."""
    if not query.strip():
        return docs
    needle = query.casefold()
    if mode == "regex":
        try:
            pattern = re.compile(needle)
        except re.error:
            return []
        return [d for d in docs if pattern.search(haystack(d, fields))]
    if mode == "substring":
        return [d for d in docs if needle in haystack(d, fields)]
    # fuzzy: every character in order, like fzf
    tight = needle.replace(" ", "")
    return [d for d in docs if is_subsequence(tight, haystack(d, fields))]


# ── sorting ─────────────────────────────────────────────────────────────────


def resolve(doc: Document, dotted: str) -> Any:
    """`author_list.0.family` -> the value, or None. Dotted paths matter because
    `author` is a formatted string and people want the first author's surname."""
    node: Any = doc
    for part in dotted.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            node = node[index] if -len(node) <= index < len(node) else None
        else:
            return None
        if node is None:
            return None
    return node


def _comparable(value: Any) -> tuple[int, float, str]:
    """Make mixed types orderable: numbers before strings, strings case-folded."""
    if isinstance(value, bool):
        return (0, float(value), "")
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    text = str(value)
    try:
        return (0, float(text), "")
    except ValueError:
        return (1, 0.0, text.casefold())


def sort(
    docs: list[Document],
    key: str,
    *,
    reverse: bool = False,
    tiebreak: str | None = None,
    null_ordering: str = "last",
) -> list[Document]:
    """Sort, keeping documents that lack the key at one end whatever the direction."""
    if tiebreak and tiebreak != key:
        docs = sort(docs, tiebreak, null_ordering=null_ordering)  # stable: secondary first

    present = [d for d in docs if resolve(d, key) is not None]
    missing = [d for d in docs if resolve(d, key) is None]
    present.sort(key=lambda d: _comparable(resolve(d, key)), reverse=reverse)
    return missing + present if null_ordering == "first" else present + missing


def discover_keys(docs: list[Document]) -> list[str]:
    """Union of top-level keys across the library, for the sort picker."""
    keys: set[str] = set()
    for doc in docs:
        keys |= {k for k in doc if not k.startswith("_")}
    return sorted(keys)


# ── display ─────────────────────────────────────────────────────────────────

# ponytail: brace/dollar stripping covers `{B}ERT` and `$\ell_2$` well enough;
# swap in pylatexenc if real macro rendering is ever wanted. Display only — the
# verbatim value is what gets stored, yanked and exported.
_LATEX = re.compile(r"[{}$]|\\[a-zA-Z]+")


def fit(text: str, width: int) -> str:
    """Truncate to `width` terminal cells, ellipsis included in the budget.

    Cells, not characters: CJK and emoji are two columns wide, combining marks
    are zero, and a list that counts characters overflows on a real library.

    The cut backs off to a word boundary — mid-word reads as a typo — and
    prefers the last colon, since a title's head is the informative part.
    Only when that boundary keeps most of the budget; otherwise the cut wins.
    """
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    head = set_cell_size(text, width - 1)
    for sep in (":", " "):
        cut = head.rfind(sep)
        if cut > 0 and cell_len(head[:cut]) >= (width - 1) * 0.6:
            return head[:cut].rstrip() + "…"
    return head + "…"


def display(value: Any) -> str:
    """Display text for a stored value. Papis keeps `tags` as a list and `str()`
    would show the Python repr."""
    if isinstance(value, (list, tuple)):
        return ", ".join(display(item) for item in value)
    return str(value)


ARXIV_DOI = "10.48550"
"""arXiv mints its own DOIs under this prefix. A DOI under any other prefix was
assigned by a publisher, which is the strongest local evidence of publication."""


def kind(doc: Document) -> str:
    """`type`, except that an arXiv-only article reads as `preprint`.

    Local data only, deliberately: an `article` whose only DOI is arXiv's and
    which names no journal, booktitle or venue has nothing to say it was ever
    published. That is evidence of absence, not absence of publication — an
    entry imported from arXiv and never refreshed looks identical, so a paper
    that did appear at a conference is flagged until its metadata is updated.
    Settling it properly needs a network lookup; see TODO § D.
    """
    if doc.get("type") != "article":
        return str(doc.get("type", ""))
    if any(str(doc.get(k) or "").strip() for k in ("journal", "booktitle", "venue")):
        return "article"
    doi = str(doc.get("doi") or "").casefold()
    return "preprint" if doi.startswith(ARXIV_DOI) else "article"


def flatten(doc: Document) -> Document:
    """The document with scalar lists joined, plus the derived `kind`, for
    `papis.format`. Lists of dicts stay as they are —
    `{doc[author_list][0][family]}` still has to index."""
    data = {
        key: display(value)
        if isinstance(value, list) and all(isinstance(item, (str, int, float)) for item in value)
        else value
        for key, value in doc.items()
    }
    data["kind"] = kind(doc)  # derived, and wins over a stored `kind` for display
    return Document(data=data)


def p90(texts: list[str]) -> int:
    """Width the 90th-percentile cell needs. Sizing a column to its longest value
    lets one outlier tax every row; sizing to the median truncates too much."""
    widths = sorted(cell_len(text) for text in texts)
    return widths[math.ceil(0.9 * len(widths)) - 1] if widths else 0  # nearest rank


def strip_latex(text: str) -> str:
    return _LATEX.sub("", text).strip()
