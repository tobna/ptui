"""`place()` — the one function that decides where a file lives.

Three callers: the add flow, `files.relocate`, and the doctor check
`file-not-canonical`. No duplicate placement logic anywhere else.

Two hard requirements from SPEC:

* **Never clobber.** The destination name is reserved with `os.link`, which
  raises `FileExistsError` — a check-then-move is TOCTOU and not acceptable.
* **File first, `info.yaml` second.** The reverse order leaves a dangling
  reference that nothing detects until the user presses `o`. If the yaml write
  fails, the caller calls `rollback()`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

import papis.format
from loguru import logger
from papis.document import Document
from slugify import slugify

type Status = Literal["ok", "already", "unmanaged", "conflict", "duplicate", "error"]

SUFFIXES = "bcdefghijklmnopqrstuvwxyz"
"""Collision suffixes are deterministic (`_b`, `_c`, …) — never a timestamp."""


@dataclass(frozen=True, slots=True)
class Rule:
    """One `[[files.rules]]` entry: which files it matches and where they go.

    Attributes:
        name: The rule's name, for messages.
        match: Filename patterns, as `fnmatch` reads them.
        operation: What happens to a matched file.
        destination: papis format string of the target path, or `None` to leave the file alone.
        path_style: How `files` stores the path.
        slugify: Slugify the target's file name.
    """

    name: str
    match: tuple[str, ...]
    operation: str = "in-place"  # move | copy | link | hardlink | in-place
    destination: str | None = None
    path_style: str = "keep"  # keep | absolute | relative-doc
    slugify: bool = False

    def matches(self, path: Path) -> bool:
        """Test a file's name against the rule's patterns.

        Args:
            path: The file; only its name counts.

        Returns:
            `True` when any pattern matches.
        """
        return any(fnmatch(path.name, pattern) for pattern in self.match)


@dataclass(frozen=True, slots=True)
class Rules:
    """The placement rules, in the order they are tried.

    Attributes:
        rules: The configured rules, first match wins.
        default: The rule for a file nothing else matches.
        pdf_root: What `{pdf_root}` expands to, or `None` when unset.
        collision: What to do when the destination exists and differs.
        hash_check: Compare bytes, so an identical destination counts as a duplicate.
    """

    rules: tuple[Rule, ...]
    default: Rule
    pdf_root: Path | None
    collision: str = "skip"  # skip | suffix | ask
    hash_check: bool = True

    def first_match(self, path: Path) -> Rule:
        """Pick the rule that governs a file.

        Args:
            path: The file.

        Returns:
            The first matching rule, or `default`.
        """
        return next((rule for rule in self.rules if rule.matches(path)), self.default)

    @classmethod
    def from_config(cls, configuration: Any) -> Rules:
        """Read the rules from the app's settings.

        Args:
            configuration: The app's `Config`.

        Returns:
            The rules, with `files.default` as the fallback.
        """

        def rule(data: dict[str, Any], name: str) -> Rule:
            """Build one rule from its TOML table.

            Args:
                data: The rule's table; `op` and `dest` are its on-disk keys.
                name: The name to use when the table has none.

            Returns:
                The rule.
            """
            return Rule(
                name=data.get("name", name),
                match=tuple(data.get("match", ["*"])),
                operation=data.get("op", "in-place"),
                destination=data.get("dest"),
                path_style=data.get("path_style", "keep"),
                slugify=data.get("slugify", False),
            )

        return cls(
            rules=tuple(
                rule(rule_table, f"rule {index}")
                for index, rule_table in enumerate(configuration.get("files.rules", []))
            ),
            default=rule(configuration.get("files.default", {}), "default"),
            pdf_root=configuration.as_path("files.pdf_root"),
            collision=configuration.get("files.collision", "skip"),
            hash_check=configuration.get("files.hash_check", True),
        )


@dataclass(frozen=True, slots=True)
class PlaceResult:
    """What `place()` did with one file.

    Attributes:
        status: The outcome.
        source: The file as it was when placed.
        rule: The name of the rule applied.
        destination: Where it went or would go; `None` when no rule moved it.
        entry: What to store in `files`; `None` leaves the existing entry alone.
        message: Why, when the status is not `ok`; empty otherwise.
    """

    status: Status
    source: Path
    rule: str
    destination: Path | None = None
    entry: str | None = None
    """What to store in `files` — None means leave the existing entry alone."""
    message: str = ""

    @property
    def moved(self) -> bool:
        """Tell whether a file landed at a destination.

        Returns:
            `True` for an `ok` status with a destination.
        """
        return self.status == "ok" and self.destination is not None


def _digest(path: Path) -> str:
    """Hash a file's bytes.

    Args:
        path: The file.

    Returns:
        The SHA-256 digest as hex text.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _existing(destination: Path) -> Path | None:
    """`destination` or the entry it would collide with on a case-insensitive filesystem.

    Args:
        destination: The wanted path.

    Returns:
        The path holding that name, or `None` when the name is free.
    """
    if destination.exists():
        return destination
    if not destination.parent.is_dir():
        return None
    lowered = destination.name.lower()
    return next((path for path in destination.parent.iterdir() if path.name.lower() == lowered), None)


def _suffixed(destination: Path) -> Path | None:
    """Find the first free `_b`, `_c`, … name for a path.

    Args:
        destination: The wanted path.

    Returns:
        The free path, or `None` when every suffix is taken.
    """
    for letter in SUFFIXES:
        candidate = destination.with_name(f"{destination.stem}_{letter}{destination.suffix}")
        if _existing(candidate) is None:
            return candidate
    return None


def _reserve(source: Path, destination: Path) -> None:
    """Create `destination` from `source` without any window in which it could be clobbered.

    Args:
        source: The existing file.
        destination: The name to create.

    Raises:
        FileExistsError: When `destination` already exists.
        OSError: When neither a link nor a copy works.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)  # raises FileExistsError — cannot clobber
    except OSError as error:
        if isinstance(error, FileExistsError):
            raise
        temporary = destination.parent / f".{destination.name}.ptui.{os.getpid()}"
        try:
            shutil.copy2(source, temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.link(temporary, destination)  # same guard
        finally:
            temporary.unlink(missing_ok=True)


def _entry(destination: Path, doc: Document, style: str, previous: str | None) -> str:
    """What `files` stores for `destination`, in `style`.

    Both sides are `normal`ised and no link is followed, so a library reached
    through a symlink gets `paper.pdf` rather than a climb out to the link's
    target, and a linked PDF stays the link. A relative entry never climbs to
    `/`: when that is its only relative path, the absolute one is written, and
    the log says so.

    Args:
        destination: The placed file.
        doc: The document it belongs to.
        style: The rule's `path_style`.
        previous: The entry as `files` holds it today, for `keep`; `None` when new.

    Returns:
        The text for the `files` entry.
    """
    folder = doc.get_main_folder()
    destination = normal(destination)
    if style == "keep" and previous is not None:
        style = "absolute" if Path(previous).is_absolute() else "relative-doc"
    if style == "relative-doc" and folder:
        home = normal(Path(folder))
        if os.path.commonpath([destination, home]) != destination.anchor:
            return os.path.relpath(destination, home)
        logger.info(f"{destination}: written absolute, the only path to it from {home} climbs to {destination.anchor}")
    return str(destination)


def normal(path: Path) -> Path:
    """Make a path absolute and free of `..`, working from the text alone.

    A symlink is not followed, so a linked file or folder keeps the name it was given.

    Args:
        path: Any path.

    Returns:
        The normalised path.
    """
    return Path(os.path.normpath(os.path.abspath(path)))


def same_file(first: Path, second: Path) -> bool:
    """Tell whether two paths spell one file, like `../../pdfs/x.pdf` and `/data/pdfs/x.pdf`.

    They do when they are the same path once normalised, or the same target once links are followed.

    Args:
        first: One path.
        second: The other path.

    Returns:
        `True` when both name one file.
    """
    return normal(first) == normal(second) or first.resolve() == second.resolve()


def entries(doc: Document) -> list[str]:
    """`files`, entry by entry as written.

    A hand-written `files: paper.pdf` is legal YAML and papis reads it as one entry
    (`Document.get_files`); iterating the string would give its letters.

    Args:
        doc: The document to read.

    Returns:
        The entries as stored; empty when it has none.
    """
    value = doc.get("files")
    if value in (None, ""):
        return []
    return list(value) if isinstance(value, list) else [value]


def resolve(doc: Document, entry: str) -> Path:
    """A `files` entry as an absolute path (entries may be relative to the doc folder).

    Args:
        doc: The document the entry belongs to.
        entry: One `files` entry; `~` is expanded.

    Returns:
        The absolute path, or the relative one when the document has no folder.
    """
    path = Path(entry).expanduser()
    folder = doc.get_main_folder()
    return path if path.is_absolute() or not folder else Path(folder) / path


def target(doc: Document, source: Path, rule: Rule, rules: Rules, *, default: str | None = None) -> Path:
    """The path `rule` wants `source` at. Pure — touches nothing.

    `default` fills in missing document keys instead of raising; the add-flow
    preview uses it so a half-filled form still shows a path.

    Args:
        doc: The document naming the file.
        source: The file; its name is used when the rule has no destination.
        rule: The governing rule.
        rules: The rule set, for `pdf_root`.
        default: Text for missing document keys, or `None` to raise on one.

    Returns:
        The wanted path, with `~` expanded and the name slugified when the rule says so.
    """
    formatted = papis.format.format(
        rule.destination or source.name,
        doc,
        additional={"pdf_root": str(rules.pdf_root or "")},
        default=default,
    )
    destination = Path(formatted).expanduser()
    if rule.slugify:
        destination = destination.with_name(
            slugify(destination.stem, separator="_", lowercase=False) + destination.suffix
        )
    return destination


def place(
    doc: Document,
    source_path: Path | str,
    rules: Rules,
    *,
    force: bool = False,
    dry_run: bool = False,
    previous: str | None = None,
) -> PlaceResult:
    """Move `source_path` where the rules say it belongs.

    Idempotent: a second run returns `already`, which is what makes migrating a
    whole library safe. `force` turns a `conflict` into a suffixed name instead
    of a skip. `previous` is the entry as it is written in `files` today, so the
    `keep` path style can be preserved.

    Args:
        doc: The document the file belongs to.
        source_path: The file, absolute or relative to the document's folder.
        rules: The placement rules.
        force: Take a suffixed name on a conflict instead of skipping.
        dry_run: Work the outcome out without touching the file.
        previous: The entry as `files` holds it today, or `None` when new.

    Returns:
        The outcome; every failure is a status, never an exception.
    """
    source = resolve(doc, str(source_path))
    rule = rules.first_match(source)

    if rule.operation == "in-place" or not rule.destination:
        return PlaceResult("unmanaged", source, rule.name, message="rule keeps files in place")
    if not source.exists():
        return PlaceResult("error", source, rule.name, message="source does not exist")

    try:
        destination = target(doc, source, rule, rules)
    except Exception as error:
        # A naming scheme the document cannot satisfy is one document's problem,
        # not the batch's: report it and let the other documents through.
        return PlaceResult("error", source, rule.name, message=f"cannot name destination: {error}")

    clash = _existing(destination)
    if clash is not None:
        # A link to nothing holds the name but has no file to compare with: it
        # is a clash like any other, never something to stat or hash.
        broken = not clash.exists()
        if not broken and (source.resolve() == clash.resolve() or os.path.samefile(source, clash)):
            entry = _entry(clash, doc, rule.path_style, previous)
            return PlaceResult("already", source, rule.name, destination=clash, entry=entry)
        if not broken and rules.hash_check and _digest(source) == _digest(clash):
            return PlaceResult(
                "duplicate", source, rule.name, destination=clash, message="destination is byte-identical"
            )
        if rules.collision == "suffix" or force:
            suffixed = _suffixed(destination)
            if suffixed is None:
                return PlaceResult("conflict", source, rule.name, destination=destination, message="no free suffix")
            destination = suffixed
        else:
            why = "destination is a broken link" if broken else "destination exists, differs"
            return PlaceResult("conflict", source, rule.name, destination=destination, message=why)

    entry = _entry(destination, doc, rule.path_style, previous)
    if dry_run:
        return PlaceResult("ok", source, rule.name, destination=destination, entry=entry)

    try:
        _reserve(source, destination)
    except FileExistsError:
        return PlaceResult("conflict", source, rule.name, destination=destination, message="destination appeared")
    except OSError as error:
        return PlaceResult("error", source, rule.name, destination=destination, message=str(error))

    if rule.operation in ("move", "copy"):
        if rule.operation == "move":
            source.unlink()
        return PlaceResult("ok", source, rule.name, destination=destination, entry=entry)
    if rule.operation == "link":  # destination is a hardlink so far; replace it with a symlink
        destination.unlink()
        destination.symlink_to(source)
    return PlaceResult("ok", source, rule.name, destination=destination, entry=entry)


def adopt(source: Path, folder: Path) -> Path:
    """Move a file into a folder under its own name, `_b`-suffixed on a clash.

    For a file the rules leave in place whose own folder is about to go (a merge's loser). Never
    clobbers; a byte-identical file already there is the file, and `source` is left alone.

    Args:
        source: The file to move.
        folder: The folder it joins.

    Returns:
        The path the file now has, or the identical file already there.

    Raises:
        FileExistsError: When every suffix is taken.
    """
    destination = folder / source.name
    if (clash := _existing(destination)) is not None:
        if clash.exists() and _digest(clash) == _digest(source):
            return clash
        suffixed = _suffixed(destination)
        if suffixed is None:
            raise FileExistsError(f"no free name for {source.name} in {folder}")
        destination = suffixed
    _reserve(source, destination)
    source.unlink()
    return destination


def holding(doc: Document, source: Path) -> Path | None:
    """The file of `doc` with the same bytes as `source`, if it already has one.

    Args:
        doc: The document to search.
        source: The file to compare.

    Returns:
        The matching file of `doc`, or `None`.
    """
    digest = _digest(source)
    held = (resolve(doc, entry) for entry in entries(doc))
    return next((path for path in held if path.is_file() and _digest(path) == digest), None)


def rollback(result: PlaceResult) -> None:
    """Undo a successful `place()` — when the `info.yaml` write fails, and on `u`.

    A source that exists is the original of a copy or a link, and the placed
    file is dropped — unless its bytes differ: then another file has taken the
    name since (or the copy was annotated), and deleting the placed one would
    lose it, so this raises and keeps it.

    Args:
        result: The outcome of the `place()` to undo; one that moved nothing is ignored.

    Raises:
        FileExistsError: When the placed file's bytes differ from the source's.
    """
    if not result.moved or result.destination is None:
        return
    if not result.source.exists():
        _reserve(result.destination, result.source)
    elif result.destination.exists() and _digest(result.source) != _digest(result.destination):
        raise FileExistsError(f"{result.source} is another file now; {result.destination} is kept")
    result.destination.unlink(missing_ok=True)


def trash(folder: Path, trash_dir: Path) -> Path:
    """Move a document folder out of the library, recoverably.

    SPEC: *files always route through trash*, whatever `undo.strategy` is. A
    merge removes the folders it folded in, and "removed" here means "no longer
    in the library and still on disk" — nothing in ptui deletes a folder outright
    while `doc.delete` and `app.undo` do not exist.

    The destination is suffixed deterministically on collision (`_b`, `_c`, …),
    like every other name ptui reserves, so trashing the same ref twice is safe.

    Args:
        folder: The document folder.
        trash_dir: The trash folder; `~` is expanded and it is created when missing.

    Returns:
        The folder's path in the trash.

    Raises:
        FileExistsError: When every suffix is taken.
    """
    trash_dir = trash_dir.expanduser()
    trash_dir.mkdir(parents=True, exist_ok=True)
    destination = trash_dir / folder.name
    for suffix in ("", *(f"_{chr(code_point)}" for code_point in range(ord("b"), ord("z") + 1))):
        candidate = trash_dir / f"{folder.name}{suffix}"
        if not candidate.exists():
            destination = candidate
            break
    else:
        raise FileExistsError(f"{trash_dir} already holds every name for {folder.name}")
    shutil.move(str(folder), str(destination))
    trashed_at(trash_dir, {destination.name: time.time()})
    return destination


TRASH_INDEX = ".ptui-trashed.json"
"""When each entry went into the trash. A move keeps the folder's own mtime, so
that cannot say how long something has been there — and guessing low deletes a
folder trashed today because it was last edited months ago."""


def trashed_at(trash_dir: Path, stamp: dict[str, float] | None = None) -> dict[str, float]:
    """Read the trash index, merging `stamp` in and dropping entries that are gone.

    An undo moves entries back, and those are gone. An entry the index has never seen, trashed before
    there was an index, is stamped now, so it gets the full retention.

    Args:
        trash_dir: The trash folder.
        stamp: Entry name to the time it was trashed, or `None` to add nothing.

    Returns:
        Entry name to the epoch seconds it was trashed at; also written back to the index.
    """
    path = trash_dir / TRASH_INDEX
    try:
        index = json.loads(path.read_text())
    except (OSError, ValueError):
        index = {}
    now = time.time()
    present = {entry.name for entry in trash_dir.iterdir() if entry.name != TRASH_INDEX}
    index = {name: index.get(name, now) for name in present} | (stamp or {})
    temporary = path.with_name(f"{TRASH_INDEX}.tmp")
    temporary.write_text(json.dumps(index, indent=1))
    temporary.replace(path)
    return index
