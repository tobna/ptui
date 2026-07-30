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

from papis.document import Document

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
    """
    seen: dict[str, Document] = {}
    for doc in docs:
        key = str(doc.get("ref") or "").strip() or f"(no ref) {doc.get('title', '?')}"
        seen.setdefault(key, doc)
    return list(seen.items())


@dataclass(frozen=True, slots=True)
class Plan:
    """What a merge would do, before anybody is asked anything."""

    survivor: Document
    others: list[Document]
    gaps: dict[str, Any] = field(default_factory=dict)
    """Keys only the others had. Filled silently — there is nothing to choose."""
    clashes: dict[str, list[Any]] = field(default_factory=dict)
    """Keys where records disagree, survivor's value first. One question each."""
    time_added: str | None = None

    @property
    def questions(self) -> list[str]:
        return list(self.clashes)


def _values(docs: list[Document], key: str) -> list[Any]:
    """Distinct values for `key`, in document order, blanks ignored."""
    out: list[Any] = []
    for doc in docs:
        value = doc.get(key)
        if value in (None, "", [], {}):
            continue
        if value not in out:
            out.append(value)
    return out


def plan(survivor: Document, others: list[Document]) -> Plan:
    """Work out which keys fill a gap and which need a decision."""
    group = [survivor, *others]
    keys = {key for doc in group for key in doc if key not in SKIPPED and not key.startswith("_")}

    gaps: dict[str, Any] = {}
    clashes: dict[str, list[Any]] = {}
    for key in sorted(keys):
        values = _values(group, key)
        if not values:
            continue
        if len(values) == 1:
            # everyone who has it agrees; if the survivor lacked it, that is a gap
            if survivor.get(key) in (None, "", [], {}):
                gaps[key] = values[0]
            continue
        clashes[key] = values

    stamps = [str(doc.get("time-added") or "") for doc in group]
    earliest = min((s for s in stamps if s), default=None)
    return Plan(survivor, others, gaps, clashes, earliest)


def resolve(plan: Plan, choices: dict[str, Any]) -> dict[str, Any]:
    """The fields to write onto the survivor.

    `choices` answers the clashes; anything unanswered keeps the survivor's own
    value, so an abandoned merge changes nothing it was not asked about.
    """
    data: dict[str, Any] = dict(plan.gaps)
    for key in plan.clashes:
        if key in choices:
            data[key] = choices[key]
    if plan.time_added and plan.time_added != str(plan.survivor.get("time-added") or ""):
        data["time-added"] = plan.time_added
    return data


def file_entries(docs: list[Document]) -> list[tuple[Document, str]]:
    """Every `files` entry across the group, with the document it came from.

    The document matters: an entry may be relative to *its own* folder, and that
    folder is about to be trashed, so the caller has to resolve it before then.
    """
    return [(doc, entry) for doc in docs for entry in (doc.get("files") or [])]
