"""The bridge to `papis doctor`. Reports; only fixes when told to.

Papis makes the wrong thing the default: `papis.commands.doctor.run(doc, checks)`
takes `fix=True` and mutates the document as a side effect of *looking* at it.
Nothing here calls it. Findings come from each check's own `operate`, which is
read-only, and a fix is applied one finding at a time through `safewrite`.
"""

from __future__ import annotations

import copy
from typing import Any

import papis.doctor as _doctor
import papis.document
from papis.document import Document

from ptui import library, safewrite

Finding = _doctor.Error

LIBRARY_WIDE = frozenset({"duplicated-keys"})
"""Checks that only mean anything over a whole query, run once.

`duplicated_keys_check` accumulates every value it has seen into papis's
module-level `DUPLICATED_KEYS_SEEN`, so a *second* look at the same document
finds its own values from the first and invents a finding. Measured against
papis 0.16: it is the only registered check with module state — despite its
name, `duplicated-values` looks for repeats *inside* one list field and is
per-document and stateless.
"""

CACHE: dict[str, tuple[int, list[Finding], list[str]]] = {}
"""`papis_id` -> (`info.yaml` mtime, findings, the names of the checks run).

Stamped with the mtime rather than invalidated by whoever writes: every write
goes through `safewrite` or `place`, and both of them touch the file, so a
`stat` answers the question that a notification would — and cannot be forgotten
at a new call site.
"""

STALE: dict[str, Document] = {}
"""`papis_id` -> a document `peek` found written since its scan, for `recheck`."""


def check_names(configured: list[str] | None = None, *, library_wide: bool = False) -> list[str]:
    """The checks to run: whatever is configured, else every registered one.

    Empty means all, so the shipped config needs no list to keep in step with
    whichever checks a papis release happens to register. The two sets are
    disjoint — `library_wide` selects the `LIBRARY_WIDE` ones, and the default
    selects everything else.

    Args:
        configured: The check names asked for; `None` or empty means all.
        library_wide: Select the `LIBRARY_WIDE` checks instead of the per-document ones.

    Returns:
        The check names to run, sorted when none were configured, else in configured order.
    """
    known = set(_doctor.registered_checks_names())
    configured = [
        _doctor.DEPRECATED_CHECK_NAMES.get(check_name, check_name) for check_name in configured or []
    ]  # `key-type` -> `field-type`
    names = sorted(known) if not configured else [check_name for check_name in configured if check_name in known]
    return [name for name in names if (name in LIBRARY_WIDE) == library_wide]


def unknown_checks(configured: list[str] | None) -> list[str]:
    """Configured names papis does not know — worth telling the user about once.

    Args:
        configured: The check names asked for, or `None`.

    Returns:
        The names neither registered nor a deprecated alias, in configured order.
    """
    known = set(_doctor.registered_checks_names()) | set(_doctor.DEPRECATED_CHECK_NAMES)
    return [name for name in configured or [] if name not in known]


def findings(doc: Document, checks: list[str] | None = None) -> list[Finding]:
    """Every per-document finding. Read-only: no fix runs, nothing is written.

    `LIBRARY_WIDE` checks are excluded — running one against a single document
    is what made a second look invent findings; `scan_library` is their pass.

    Args:
        doc: The document to check.
        checks: The check names asked for; `None` or empty means all.

    Returns:
        Every papis `Error` the checks report.
    """
    return _operate(doc, check_names(checks))


def _operate(doc: Document, names: list[str]) -> list[Finding]:
    """The named checks, already resolved — an empty list runs nothing here.

    Args:
        doc: The document to check.
        names: Resolved check names.

    Returns:
        Every papis `Error` those checks report, in `names` order.
    """
    return [error for name in names for error in _doctor.REGISTERED_CHECKS[name].operate(doc)]


def scan(doc: Document, checks: list[str] | None = None) -> list[Finding]:
    """`findings` for `checks`, cached against `info.yaml`'s mtime for the info pane to read.

    Returns only what those checks found.

    Only their findings are replaced in the cache entry: replacing the whole of
    it made `:doctor.run refs` wipe every other check's marker until `! d`.

    A document written since its last scan is re-read first, in place: the
    stamp names the file, so the findings must come from the file and not from
    the copy loaded before it changed. In place, so a fix can act on what is
    returned — which makes this the main thread's call (`load` empties the
    document before refilling it, under whoever is reading it).

    Args:
        doc: The document to check; reloaded in place when written since its last scan.
        checks: The check names asked for; `None` or empty means all.

    Returns:
        The findings of those checks alone.
    """
    stamp = library.mtime(doc)  # before the read: the data is then at least this new
    hit = CACHE.get(library.doc_id(doc))
    if hit is not None and hit[0] != stamp:
        doc.load()
    return _scan(doc, check_names(checks), stamp)


def _from_disk(doc: Document) -> Document:
    """The document as its file holds it now, in a new object.

    `cached` runs in `recheck`'s thread, where reloading the redraw's own copy would empty it
    under the redraw.

    Args:
        doc: The document to read again.

    Returns:
        A new document from its folder, or `doc` itself when it has no folder.
    """
    folder = doc.get_main_folder()
    return papis.document.from_folder(folder) if folder else doc


def _scan(doc: Document, names: list[str], stamp: int) -> list[Finding]:
    """Run the named checks and store the result in the document's cache entry.

    Args:
        doc: The document to check.
        names: Resolved check names.
        stamp: The `info.yaml` mtime the cache entry is stamped with.

    Returns:
        The findings of `names` alone.
    """
    key = library.doc_id(doc)
    hit = CACHE.get(key)
    others = [name for name in hit[2] if name not in names] if hit else []
    # A stale entry's other checks run again too: the new stamp vouches for them.
    kept = (
        [finding for finding in hit[1] if finding.name in others] if hit and hit[0] == stamp else _operate(doc, others)
    )
    found = _operate(doc, names)
    CACHE[key] = (stamp, sorted([*kept, *found], key=lambda finding: finding.name), sorted({*others, *names}))
    return found


def cached(doc: Document) -> list[Finding] | None:
    """Read this document's findings, or `None` if it was never scanned.

    `None` is "unknown", which is not the same as "clean".

    A document written since its scan is re-checked here, with the checks it
    was scanned with, on a fresh read of its file (`_from_disk`): checking the
    copy in memory stamped findings from before an outside edit with the mtime
    from after it, so they never went stale again.

    Args:
        doc: The document to read.

    Returns:
        Its findings sorted by check name, or `None` when it was never scanned.
    """
    hit = CACHE.get(library.doc_id(doc))
    if hit is None:
        return None
    stamp = library.mtime(doc)
    return hit[1] if hit[0] == stamp else _scan(_from_disk(doc), hit[2], stamp)


def peek(doc: Document, checks: tuple[str, ...] | None = None) -> list[Finding] | None:
    """Read the findings for a redraw, which never runs a check.

    A document written since its scan keeps showing what that scan found and is queued for `recheck`.
    `checks` keeps only those checks' findings: the doctor view of a named run.

    At ~2 ms a document, re-checking inside the redraw froze the first one after a mass change on
    disk, a sync or a `git pull`, for as long as it took.

    Args:
        doc: The document to read.
        checks: The check names to keep, or `None` for all.

    Returns:
        The findings the last scan stored, or `None` when it was never scanned.
    """
    key = library.doc_id(doc)
    hit = CACHE.get(key)
    if hit is None:
        return None
    if hit[0] != library.mtime(doc):
        STALE[key] = doc
    return hit[1] if checks is None else [finding for finding in hit[1] if finding.name in checks]


def recheck() -> int:
    """Re-check every document `peek` queued, each with the checks it was scanned with.

    Meant for a thread; returns how many, so the caller knows whether there is anything to
    repaint.

    Returns:
        How many documents were re-checked.
    """
    done = 0
    while STALE:
        try:
            _, doc = STALE.popitem()
        except KeyError:  # another recheck took the last one between the test and the pop
            break
        cached(doc)
        done += 1
    return done


def scan_library(docs: list[Document], checks: list[str] | None = None) -> list[tuple[Document, Finding]]:
    """The `LIBRARY_WIDE` checks, over a whole set, once.

    papis's seen-values state is reset first, so the pass sees only these
    documents; a duplicate is reported on the *second* document holding the
    value, which is why this can never run per document.

    Args:
        docs: The documents to check together.
        checks: The check names asked for; `None` or empty means all.

    Returns:
        `(doc, finding)` pairs, in document order.
    """
    _doctor.DUPLICATED_KEYS_SEEN.clear()
    return [
        (doc, error)
        for doc in docs
        for name in check_names(checks, library_wide=True)
        for error in _doctor.REGISTERED_CHECKS[name].operate(doc)
    ]


def fix(doc: Document, finding: Finding) -> dict[str, tuple[Any, Any]]:
    """Apply one finding's own fix and persist it.

    Returns each key that changed with what the write replaced and what it wrote, `None` for no
    key — the two sides of its undo step.

    The check's `fix_action` mutates the in-memory document, so the keys it
    touched are diffed out and written through `safewrite` — which re-checks the
    mtime, keeps unknown keys and comments, and resyncs the papis cache. On any
    failure the document is reloaded from disk, or it would keep an edit that
    never reached the file.

    Args:
        doc: The document the finding is about; reloaded from disk when the write fails.
        finding: One finding from a check.

    Returns:
        Changed key to `(old value, new value)`, sorted by key; empty when nothing changed.

    Raises:
        StaleError: When `info.yaml` changed on disk.
    """
    if finding.fix_action is None:
        return {}
    # deep: papis's fixes edit lists and author dicts in place (`files`, initials),
    # and a shallow copy shares them, so the diff saw no change and wrote nothing
    before = copy.deepcopy(dict(doc))
    finding.fix_action()
    written = {key: safewrite.plain(value) for key, value in doc.items() if before.get(key) != value}
    written |= {key: None for key in before if key not in doc}
    if not written:
        return {}
    replaced: dict[str, Any] = {}

    def mutate(data: Any) -> None:
        """Write the fix's changes into the file, remembering what each replaced.

        Args:
            data: The ruamel map of `info.yaml`.
        """
        for key, value in written.items():
            replaced[key] = safewrite.plain(data.get(key))  # from the file, as undo restores it
            if value is None:
                safewrite.drop(data, key)
            else:
                safewrite.put(data, key, value)

    try:
        safewrite.edit(doc, mutate)
    except BaseException:
        doc.load()  # the in-memory fix never landed; do not keep pretending it did
        raise
    return {key: (replaced[key], written[key]) for key in sorted(written)}
