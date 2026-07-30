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

from ptui import safewrite

Finding = _doctor.Error


def check_names(configured: list[str] | None = None) -> list[str]:
    """The checks to run: whatever is configured, else every registered one.

    Empty means all, so the shipped config needs no list to keep in step with
    whichever checks a papis release happens to register.
    """
    known = set(_doctor.registered_checks_names())
    if not configured:
        return sorted(known)
    return [name for name in configured if name in known]


def unknown_checks(configured: list[str] | None) -> list[str]:
    """Configured names papis does not know — worth telling the user about once."""
    known = set(_doctor.registered_checks_names())
    return [name for name in configured or [] if name not in known]


def findings(doc: Document, checks: list[str] | None = None) -> list[Finding]:
    """Every finding for one document. Read-only: no fix runs, nothing is written."""
    return [
        error
        for name in check_names(checks)
        for error in _doctor.REGISTERED_CHECKS[name].operate(doc)
    ]


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
