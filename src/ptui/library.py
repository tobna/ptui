"""The two query layers, sorting, and display text.

SPEC keeps these strictly apart and so does this module:

* **scope** — a real papis query, run against the database on submit. What it
  supports depends on the papis backend.
* **narrow** — an in-memory filter over the scoped set. Backend-independent,
  identical on every machine.

Sorting applies to the scoped set and is independent of narrowing.
"""

from __future__ import annotations

import os
import re
from typing import Any

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


def _is_subsequence(needle: str, hay: str) -> bool:
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
    return [d for d in docs if _is_subsequence(tight, haystack(d, fields))]


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


def strip_latex(text: str) -> str:
    return _LATEX.sub("", text).strip()
