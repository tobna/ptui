"""The bridge to `papis doctor`. Reports; only fixes when told to.

Papis makes the wrong thing the default: `papis.commands.doctor.run(doc, checks)`
takes `fix=True` and mutates the document as a side effect of *looking* at it.
Nothing here calls it. Findings come from each check's own `operate`, which is
read-only, and a fix is applied one finding at a time through `safewrite`.
"""

from __future__ import annotations

from pathlib import Path

import papis.commands.doctor as _doctor
from papis.document import Document

from ptui import library, safewrite

Finding = _doctor.Error

LIBRARY_WIDE = frozenset({"duplicated-keys"})
"""Checks that only mean anything over a whole query, run once.

`duplicated_keys_check` accumulates every value it has seen into papis's
module-level `DUPLICATED_KEYS_SEEN`, so a *second* look at the same document
finds its own values from the first and invents a finding. Measured against
papis 0.15: it is the only registered check with module state — despite its
name, `duplicated-values` looks for repeats *inside* one list field and is
per-document and stateless.
"""

CACHE: dict[str, tuple[float, list[Finding]]] = {}
"""`papis_id` -> (`info.yaml` mtime, findings) from the last scan.

Stamped with the mtime rather than invalidated by whoever writes: every write
goes through `safewrite` or `place`, and both of them touch the file, so a
`stat` answers the question that a notification would — and cannot be forgotten
at a new call site.
"""


def check_names(configured: list[str] | None = None, *, library_wide: bool = False) -> list[str]:
    """The checks to run: whatever is configured, else every registered one.

    Empty means all, so the shipped config needs no list to keep in step with
    whichever checks a papis release happens to register. The two sets are
    disjoint — `library_wide` selects the `LIBRARY_WIDE` ones, and the default
    selects everything else.
    """
    known = set(_doctor.registered_checks_names())
    names = sorted(known) if not configured else [n for n in configured if n in known]
    return [name for name in names if (name in LIBRARY_WIDE) == library_wide]


def unknown_checks(configured: list[str] | None) -> list[str]:
    """Configured names papis does not know — worth telling the user about once."""
    known = set(_doctor.registered_checks_names())
    return [name for name in configured or [] if name not in known]


def findings(doc: Document, checks: list[str] | None = None) -> list[Finding]:
    """Every per-document finding. Read-only: no fix runs, nothing is written.

    `LIBRARY_WIDE` checks are excluded — running one against a single document
    is what made a second look invent findings; `scan_library` is their pass.
    """
    return [
        error
        for name in check_names(checks)
        for error in _doctor.REGISTERED_CHECKS[name].operate(doc)
    ]


def scan(doc: Document, checks: list[str] | None = None) -> list[Finding]:
    """`findings`, cached against `info.yaml`'s mtime for the info pane to read."""
    found = findings(doc, checks)
    CACHE[library.doc_id(doc)] = (_stamp(doc), found)
    return found


def cached(doc: Document) -> list[Finding] | None:
    """The last scan of this document, or `None` if it was never scanned or has
    been written since. `None` is "unknown", which is not the same as "clean"."""
    hit = CACHE.get(library.doc_id(doc))
    return hit[1] if hit and hit[0] == _stamp(doc) else None


def scan_library(
    docs: list[Document], checks: list[str] | None = None
) -> list[tuple[Document, Finding]]:
    """The `LIBRARY_WIDE` checks, over a whole set, once.

    papis's seen-values state is reset first, so the pass sees only these
    documents; a duplicate is reported on the *second* document holding the
    value, which is why this can never run per document.
    """
    _doctor.DUPLICATED_KEYS_SEEN.clear()
    return [
        (doc, error)
        for doc in docs
        for name in check_names(checks, library_wide=True)
        for error in _doctor.REGISTERED_CHECKS[name].operate(doc)
    ]


def _stamp(doc: Document) -> float:
    path = info_path(doc)
    try:
        return path.stat().st_mtime if path else 0.0
    except OSError:
        return 0.0


def fix(doc: Document, finding: Finding) -> list[str]:
    """Apply one finding's own fix and persist it. Returns the keys that changed.

    The check's `fix_action` mutates the in-memory document, so the keys it
    touched are diffed out and written through `safewrite` — which re-checks the
    mtime, keeps unknown keys and comments, and resyncs the papis cache. On any
    failure the document is reloaded from disk, or it would keep an edit that
    never reached the file.
    """
    if finding.fix_action is None:
        return []
    before = dict(doc)
    finding.fix_action()
    changed = {key: value for key, value in doc.items() if before.get(key) != value}
    dropped = [key for key in before if key not in doc]
    if not changed and not dropped:
        return []

    def mutate(data: object) -> None:
        for key, value in changed.items():
            data[key] = value  # type: ignore[index]
        for key in dropped:
            data.pop(key, None)  # type: ignore[attr-defined]

    try:
        safewrite.edit(doc, mutate)
    except BaseException:
        doc.load()  # the in-memory fix never landed; do not keep pretending it did
        raise
    return sorted([*changed, *dropped])


def info_path(doc: Document) -> Path | None:
    path = doc.get_info_file()
    return Path(path) if path else None
