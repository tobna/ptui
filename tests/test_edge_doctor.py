"""Edges of the doctor bridge.

Fixes that mutate in place or remove a key, and the cache's three states (never scanned, clean,
stale).
"""

import os

import papis.database
import papis.document

from ptui import doctor


def _doc(papis_lib, body):
    """A document inside the fixture library, so `safewrite.edit` can resync it.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture.
        body: The `info.yaml` text.

    Returns:
        `(document, its info.yaml)`.
    """
    folder = papis_lib / "lib" / "edge"
    folder.mkdir()
    (folder / "info.yaml").write_text(body)
    papis.database.clear_cached()
    return papis.document.from_folder(str(folder)), folder / "info.yaml"


def _fixable(doc, check):
    """Find the first finding of a check that has a fix.

    Args:
        doc: The document.
        check: The check's name.

    Returns:
        The finding.
    """
    return next(finding for finding in doctor.findings(doc, [check]) if finding.fix_action)


def test_a_fix_that_renames_a_key_drops_the_old_one_and_keeps_the_comment_after_it(papis_lib):
    """A fix that renames a key drops the old one and keeps the comment after it.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = _doc(papis_lib, "title: T\nschool: S\n# about ref\nref: r\npapis_id: edge\n")
    assert doctor.fix(doc, _fixable(doc, "biblatex-key-alias")) == {"institution": (None, "S"), "school": ("S", None)}
    assert info.read_text() == "title: T\n# about ref\nref: r\npapis_id: edge\ninstitution: S\n"


def test_fixing_a_missing_file_removes_it_from_info_yaml(papis_lib):
    """Fixing a missing file removes it from info yaml.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = _doc(papis_lib, "title: T\nfiles:\n- gone.pdf\npapis_id: edge\n")
    assert list(doctor.fix(doc, _fixable(doc, "files"))) == ["files"]
    assert "gone.pdf" not in info.read_text()


def test_fixing_initials_writes_author_list_too(papis_lib):
    """Fixing initials writes author list too.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = _doc(
        papis_lib,
        "title: T\nauthor: Doe, J R\nauthor_list:\n- family: Doe\n  given: J R\npapis_id: edge\n",
    )
    finding = next(result for result in doctor.findings(doc, ["string-cleaner"]) if "initials" in result.msg)
    doctor.fix(doc, finding)
    papis.database.clear_cached()
    on_disk = papis.document.from_folder(str(info.parent))
    assert on_disk["author_list"][0]["given"] == doc["author_list"][0]["given"] != "J R"


def test_clean_is_an_empty_list_and_never_scanned_is_none(papis_lib):
    """Clean is an empty list and never scanned is none.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, _ = _doc(papis_lib, "title: T\nref: ok\npapis_id: edge\n")
    assert doctor.cached(doc) is None
    assert doctor.scan(doc, ["refs"]) == []
    assert doctor.cached(doc) == []  # scanned and clean: not the same answer as None


def test_an_external_edit_is_rechecked_with_the_checks_it_was_scanned_with(papis_lib):
    """An external edit is rechecked with the checks it was scanned with.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    doc, info = _doc(papis_lib, "title: T\nref: ok\npapis_id: edge\n")
    doctor.scan(doc, ["refs"])
    info.write_text("title: T\nref: bad ref!\npapis_id: edge\n")
    stat = info.stat()  # a distinct mtime, whatever the clock granularity
    os.utime(info, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    doc.load()
    assert [finding.name for finding in doctor.cached(doc)] == ["refs"]  # only `refs`, as first scanned


def test_naming_only_a_library_wide_check_runs_nothing_per_document():
    """Naming only a library wide check runs nothing per document."""
    assert doctor.check_names(["duplicated-keys"]) == []
    assert doctor.check_names(["duplicated-keys"], library_wide=True) == ["duplicated-keys"]
