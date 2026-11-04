"""Folding several documents into one. The decisions, separate from the UI.

The same paper arrives twice — once from arXiv, once from the proceedings — and
each record holds fields the other lacks. Merging is therefore mostly *union*,
with a question only where two records genuinely disagree.

Nothing here writes anything. `plan()` works out what would happen, `resolve()`
turns answers into the final field set, and the caller does the writing through
`safewrite` and `place()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import papis.document
from papis.document import Document

from ptui import place

SKIPPED = frozenset({"papis_id", "files", "time-added", "ref", "_papis_local_folder"})
"""Keys the merge never asks about as an ordinary clash.

`papis_id` and the folder identify the survivor and must not move.
`ref` is asked **first**, because choosing which citekey survives is the same
decision as choosing which document survives — see `survivor_choices`. Once that
is answered the survivor keeps its own ref, so there is nothing left to ask.
`files` is unioned rather than chosen — losing an attachment is the one outcome
here that cannot be undone from the metadata. `time-added` is resolved to the
earliest of the group: the document has existed since whichever copy came first.
"""


def survivor_choices(docs: list[Document]) -> list[tuple[str, Document]]:
    """`(ref, doc)` per distinct citekey. Picking one picks the survivor.

    The document that keeps its `ref` is the one that keeps its folder and its
    `papis_id`, so one question settles both. Documents with no ref at all are
    offered under their title, since something has to identify them.

    Args:
        docs: The documents being merged, in the order they were picked.

    Returns:
        One `(ref, doc)` pair per distinct citekey, in document order; `doc` is the first holder.
    """
    seen: dict[str, Document] = {}
    for doc in docs:
        key = str(doc.get("ref") or "").strip() or f"(no ref) {doc.get('title', '?')}"
        seen.setdefault(key, doc)
    return list(seen.items())


@dataclass(frozen=True, slots=True)
class Plan:
    """What a merge would do, before anybody is asked anything.

    Attributes:
        survivor: The document that keeps its folder and `papis_id`.
        others: The documents folded into it.
        gaps: Key to value, for keys only the others had.
        clashes: Key to its distinct values, survivor's first, for keys where records disagree.
        time_added: The earliest `time-added` of the group, or `None` when no one has one.
    """

    survivor: Document
    others: list[Document]
    gaps: dict[str, Any] = field(default_factory=dict)
    """Keys only the others had. Filled silently — there is nothing to choose."""
    clashes: dict[str, list[Any]] = field(default_factory=dict)
    """Keys where records disagree, survivor's value first. One question each."""
    time_added: str | None = None

    @property
    def questions(self) -> list[str]:
        """The keys that need an answer.

        Returns:
            The keys of `clashes`, one question each.
        """
        return list(self.clashes)


BLANK = (None, "", [], {})


def _authors(doc: Document) -> Any:
    """Read a document's authors as a list.

    Falls back on the list papis makes of a bare `author` string, so a record holding only the string
    still has a side when the lists disagree.

    Args:
        doc: The document to read.

    Returns:
        `author_list`'s value, a list of author dicts built from `author`, or `None` when it has
        neither.
    """
    if doc.get("author_list") not in BLANK:
        return doc["author_list"]
    return papis.document.split_authors_name([str(doc["author"])]) if doc.get("author") not in BLANK else None


def _values(docs: list[Document], key: str) -> list[Any]:
    """Collect the distinct values for `key`, in document order, blanks ignored.

    Compared as text: a fetched `year: 2017` and a stored `year: '2017'` are no question, and two
    author lists naming the same people are one, whatever else they hold.

    Args:
        docs: The group of documents, survivor first.
        key: The `info.yaml` key to compare.

    Returns:
        Each distinct value once, as the first document holding it stores it.
    """

    def text(value: Any) -> str:
        """Reduce a value to the text it is compared by.

        Args:
            value: A value of `key`.

        Returns:
            The authors joined by papis for `author_list`, else `str(value)`.
        """
        return papis.document.author_list_to_author({"author_list": value}) if key == "author_list" else str(value)

    distinct: list[Any] = []
    for doc in docs:
        value = _authors(doc) if key == "author_list" else doc.get(key)
        if value in BLANK:
            continue
        if text(value) not in {text(seen) for seen in distinct}:
            distinct.append(value)
    return distinct


def plan(survivor: Document, others: list[Document]) -> Plan:
    """Work out which keys fill a gap and which need a decision.

    Args:
        survivor: The document that survives the merge.
        others: The documents folded into it.

    Returns:
        The `Plan`; nothing is written.
    """
    group = [survivor, *others]
    keys = {key for doc in group for key in doc if key not in SKIPPED and not key.startswith("_")}
    if "author_list" in keys:  # `author` is derived from it in `resolve`, not asked twice
        keys.discard("author")

    gaps: dict[str, Any] = {}
    clashes: dict[str, list[Any]] = {}
    for key in sorted(keys):
        values = _values(group, key)
        if not values:
            continue
        if len(values) == 1:
            # everyone who has it agrees; if the survivor lacked it, that is a
            # gap — filled with a record's own value, never the derived authors
            filler = next((doc[key] for doc in others if doc.get(key) not in BLANK), None)
            if survivor.get(key) in BLANK and filler is not None:
                gaps[key] = filler
            continue
        clashes[key] = values

    stamps = [str(doc.get("time-added") or "") for doc in group]
    earliest = min((stamp for stamp in stamps if stamp), default=None)
    return Plan(survivor, others, gaps, clashes, earliest)


def resolve(plan: Plan, choices: dict[str, Any]) -> dict[str, Any]:
    """The fields to write onto the survivor.

    `choices` answers the clashes; anything unanswered keeps the survivor's own
    value, so an abandoned merge changes nothing it was not asked about.

    Args:
        plan: The merge as `plan()` worked it out.
        choices: Key of a clash to the value chosen for it.

    Returns:
        Key to value, for every field the survivor must change.
    """
    data: dict[str, Any] = dict(plan.gaps)
    for key in plan.clashes:
        if key in choices:
            data[key] = choices[key]
    if plan.time_added and plan.time_added != str(plan.survivor.get("time-added") or ""):
        data["time-added"] = plan.time_added
    if "author_list" in data:
        data["author"] = papis.document.author_list_to_author(data)
    return data


def file_entries(docs: list[Document]) -> list[tuple[Document, str]]:
    """Every `files` entry across the group, with the document it came from.

    The document matters: an entry may be relative to *its own* folder, and that
    folder is about to be trashed, so the caller has to resolve it before then.

    Args:
        docs: The group of documents.

    Returns:
        One `(doc, entry)` pair per `files` entry, as stored, in document order.
    """
    return [(doc, entry) for doc in docs for entry in place.entries(doc)]
