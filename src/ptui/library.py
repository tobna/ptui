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
import shlex
from dataclasses import dataclass
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
    return " ".join(display(doc.get(f, "")) for f in fields).casefold()


def is_subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(char in it for char in needle)


FUZZY_SPAN = 3
"""How far a fuzzy match may spread, as a multiple of the needle. Plain
subsequence matching is useless on real data — `note` matches
`Locality-Atte(n)ding visi(o)n (T)ransform(e)r` — so the run has to stay tight."""


def fuzzy_match(needle: str, hay: str) -> bool:
    """Subsequence, but only when the matched characters sit close together.

    Scans from every possible start so a late tight run still counts; the needle
    is short and the haystack is one document, so the quadratic worst case never
    shows up in practice.
    """
    if not needle:
        return True
    budget = len(needle) * FUZZY_SPAN
    for start in range(len(hay)):
        if hay[start] != needle[0]:
            continue
        index = 0
        for offset in range(start, min(len(hay), start + budget)):
            if hay[offset] == needle[index]:
                index += 1
                if index == len(needle):
                    return True
    return False


_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):(?!/)(.*)$")
_NUM = r"(\d+(?:\.\d+)?)?"
_RANGE = re.compile(rf"^(>=|<=|>|<)?{_NUM}(\.\.)?{_NUM}$")


@dataclass(frozen=True, slots=True)
class Term:
    """One whitespace-separated word of a narrow query."""

    text: str
    field: str = ""
    """Empty means "any of the configured narrow fields"."""
    negate: bool = False


def parse_query(query: str, aliases: dict[str, str] | None = None) -> tuple[Term, ...]:
    """`-survey a:nauen "vision transformer"` -> three terms, ANDed by the caller.

    Quotes come from `shlex`, which raises on the unbalanced quote you always
    have halfway through typing one — so an unterminated query is retried closed.
    """
    query = expand_aliases(query, aliases or {})
    tokens: list[str] = query.split()
    for candidate in (query, f'{query}"', f"{query}'"):
        try:
            tokens = shlex.split(candidate)
            break
        except ValueError:
            continue

    terms = []
    for token in tokens:
        negate = token.startswith("-") and len(token) > 1
        token = token[1:] if negate else token
        # `http://x` must not read as the field `http`, hence the `(?!/)`
        found = _FIELD.match(token)
        field, text = (found.group(1), found.group(2)) if found else ("", token)
        if text:
            terms.append(Term(text.casefold(), field, negate))
    return tuple(terms)


def _in_range(value: str, spec: str) -> bool | None:
    """`year:>2023`, `year:2020..2024`, `year:..2020`. None when this is not a
    range comparison at all, so the caller falls back to a substring test."""
    found = _RANGE.match(spec)
    if not found or not (found.group(1) or found.group(3)):
        return None
    try:
        number = float(re.sub(r"[^0-9.]", "", value) or "nan")
        low = float(found.group(2)) if found.group(2) else float("-inf")
        high = float(found.group(4)) if found.group(4) else float("inf")
    except ValueError:
        return None
    if found.group(3):  # a .. b
        return low <= number <= high
    op, bound = found.group(1), low
    return {">": number > bound, ">=": number >= bound, "<": number < bound}.get(
        op, number <= bound
    )


def match_text(text: str, terms: tuple[Term, ...], mode: str = "substring") -> bool:
    """Every term must hit the blob. For pickers, where there are no fields."""
    hay = text.casefold()
    hit = fuzzy_match if mode == "fuzzy" else lambda n, h: n in h
    return all(hit(term.text, hay) != term.negate for term in terms)


def match_doc(doc: Document, terms: tuple[Term, ...], fields: list[str], mode: str) -> bool:
    """Every term must hit: a qualified term its own field, a bare term any of
    `fields`. Ranges only apply to a qualified term — `>2023` alone is a string."""
    blob = haystack(doc, fields)
    hit = fuzzy_match if mode == "fuzzy" else lambda n, h: n in h
    for term in terms:
        if not term.field:
            found = hit(term.text, blob)
        else:
            value = display(resolve(doc, term.field) or "")
            ranged = _in_range(value, term.text)
            found = ranged if ranged is not None else hit(term.text, value.casefold())
        if found == term.negate:
            return False
    return True


def narrow(
    docs: list[Document],
    query: str,
    fields: list[str],
    mode: str,
    aliases: dict[str, str] | None = None,
) -> list[Document]:
    """Filter in memory. Terms are ANDed, so typing more always narrows — the
    old whole-query subsequence test left 702 of 754 documents on `nauen`.
    Unparsable regex matches nothing rather than raising."""
    if not query.strip():
        return docs
    if mode == "regex":
        try:
            pattern = re.compile(query.casefold())
        except re.error:
            return []
        return [d for d in docs if pattern.search(haystack(d, fields))]
    terms = parse_query(query, aliases)
    return [d for d in docs if match_doc(d, terms, fields, mode)]


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


def wrap_cells(text: str, width: int) -> list[str]:
    """Greedy word wrap by terminal cells. A word wider than `width` gets its own
    row and is cut by the caller — splitting inside a word reads as corruption."""
    rows: list[str] = []
    row = ""
    for word in text.split():
        candidate = f"{row} {word}" if row else word
        if cell_len(candidate) <= width:
            row = candidate
            continue
        if row:
            rows.append(row)
        row = word
    if row:
        rows.append(row)
    return rows


def fit_lines(text: str, width: int, lines: int) -> str:
    """`fit`, but over `lines` rows: the result is newline-joined and every row is
    within `width` cells, so the table never has to guess where to wrap.

    Wrapping ourselves rather than letting the widget do it is what keeps the
    ellipsis honest — a greedy wrap fits fewer cells than `width * lines`, and a
    budget computed from the product silently loses the last words instead.
    """
    if lines <= 1 or width <= 0:
        return fit(text, width)
    rows = wrap_cells(text, width)
    # Every row is cut, not just the last: a single word wider than the column
    # gets a row of its own and would otherwise overflow the cell unclipped.
    if len(rows) <= lines:
        return "\n".join(fit(row, width) for row in rows)
    kept = [fit(row, width) for row in rows[: lines - 1]]
    return "\n".join([*kept, fit(" ".join(rows[lines - 1 :]), width)])


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
