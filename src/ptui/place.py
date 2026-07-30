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
import os
import shutil
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

import papis.format
from papis.document import Document
from slugify import slugify

type Status = Literal["ok", "already", "unmanaged", "conflict", "duplicate", "error"]

SUFFIXES = "bcdefghijklmnopqrstuvwxyz"
"""Collision suffixes are deterministic (`_b`, `_c`, …) — never a timestamp."""


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    match: tuple[str, ...]
    op: str = "in-place"  # move | copy | link | hardlink | in-place
    dest: str | None = None
    path_style: str = "keep"  # keep | absolute | relative-doc
    slugify: bool = False

    def matches(self, path: Path) -> bool:
        return any(fnmatch(path.name, pattern) for pattern in self.match)


@dataclass(frozen=True, slots=True)
class Rules:
    rules: tuple[Rule, ...]
    default: Rule
    pdf_root: Path | None
    collision: str = "skip"  # skip | suffix | ask
    hash_check: bool = True

    def first_match(self, path: Path) -> Rule:
        return next((r for r in self.rules if r.matches(path)), self.default)

    @classmethod
    def from_config(cls, cfg: Any) -> Rules:
        def rule(data: dict[str, Any], name: str) -> Rule:
            return Rule(
                name=data.get("name", name),
                match=tuple(data.get("match", ["*"])),
                op=data.get("op", "in-place"),
                dest=data.get("dest"),
                path_style=data.get("path_style", "keep"),
                slugify=data.get("slugify", False),
            )

        return cls(
            rules=tuple(rule(r, f"rule {i}") for i, r in enumerate(cfg.get("files.rules", []))),
            default=rule(cfg.get("files.default", {}), "default"),
            pdf_root=cfg.as_path("files.pdf_root"),
            collision=cfg.get("files.collision", "skip"),
            hash_check=cfg.get("files.hash_check", True),
        )


@dataclass(frozen=True, slots=True)
class PlaceResult:
    status: Status
    src: Path
    rule: str
    dest: Path | None = None
    entry: str | None = None
    """What to store in `files` — None means leave the existing entry alone."""
    message: str = ""

    @property
    def moved(self) -> bool:
        return self.status == "ok" and self.dest is not None


def _digest(path: Path) -> str:
    with path.open("rb") as fd:
        return hashlib.file_digest(fd, "sha256").hexdigest()


def _existing(dest: Path) -> Path | None:
    """`dest` or the entry it would collide with on a case-insensitive filesystem."""
    if dest.exists():
        return dest
    if not dest.parent.is_dir():
        return None
    lowered = dest.name.lower()
    return next((p for p in dest.parent.iterdir() if p.name.lower() == lowered), None)


def _suffixed(dest: Path) -> Path | None:
    for letter in SUFFIXES:
        candidate = dest.with_name(f"{dest.stem}_{letter}{dest.suffix}")
        if _existing(candidate) is None:
            return candidate
    return None


def _reserve(src: Path, dest: Path) -> None:
    """Create `dest` from `src` without any window in which it could be clobbered."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dest)  # raises FileExistsError — cannot clobber
    except OSError as exc:
        if isinstance(exc, FileExistsError):
            raise
        tmp = dest.parent / f".{dest.name}.ptui.{os.getpid()}"
        try:
            shutil.copy2(src, tmp)
            with tmp.open("rb") as fd:
                os.fsync(fd.fileno())
            os.link(tmp, dest)  # same guard
        finally:
            tmp.unlink(missing_ok=True)


def _entry(dest: Path, doc: Document, style: str, previous: str | None) -> str:
    folder = doc.get_main_folder()
    if style == "keep" and previous is not None:
        style = "absolute" if Path(previous).is_absolute() else "relative-doc"
    if style == "relative-doc" and folder:
        return os.path.relpath(dest, folder)
    return str(dest)


def resolve(doc: Document, entry: str) -> Path:
    """A `files` entry as an absolute path (entries may be relative to the doc folder)."""
    path = Path(entry).expanduser()
    folder = doc.get_main_folder()
    return path if path.is_absolute() or not folder else Path(folder) / path


def target(
    doc: Document, src: Path, rule: Rule, rules: Rules, *, default: str | None = None
) -> Path:
    """The path `rule` wants `src` at. Pure — touches nothing.

    `default` fills in missing document keys instead of raising; the add-flow
    preview uses it so a half-filled form still shows a path.
    """
    formatted = papis.format.format(
        rule.dest or src.name,
        doc,
        additional={"pdf_root": str(rules.pdf_root or "")},
        default=default,
    )
    dest = Path(formatted).expanduser()
    if rule.slugify:
        dest = dest.with_name(slugify(dest.stem, separator="_", lowercase=False) + dest.suffix)
    return dest


def place(
    doc: Document,
    src_path: Path | str,
    rules: Rules,
    *,
    force: bool = False,
    dry_run: bool = False,
    previous: str | None = None,
) -> PlaceResult:
    """Move `src_path` where the rules say it belongs.

    Idempotent: a second run returns `already`, which is what makes migrating a
    whole library safe. `force` turns a `conflict` into a suffixed name instead
    of a skip. `previous` is the entry as it is written in `files` today, so the
    `keep` path style can be preserved.
    """
    src = resolve(doc, str(src_path))
    rule = rules.first_match(src)

    if rule.op == "in-place" or not rule.dest:
        return PlaceResult("unmanaged", src, rule.name, message="rule keeps files in place")
    if not src.exists():
        return PlaceResult("error", src, rule.name, message="source does not exist")

    try:
        dest = target(doc, src, rule, rules)
    except Exception as exc:
        # A naming scheme the document cannot satisfy is one document's problem,
        # not the batch's: report it and let the other documents through.
        return PlaceResult("error", src, rule.name, message=f"cannot name destination: {exc}")

    clash = _existing(dest)
    if clash is not None:
        if src.resolve() == clash.resolve() or os.path.samefile(src, clash):
            entry = _entry(clash, doc, rule.path_style, previous)
            return PlaceResult("already", src, rule.name, dest=clash, entry=entry)
        if rules.hash_check and _digest(src) == _digest(clash):
            return PlaceResult(
                "duplicate", src, rule.name, dest=clash, message="destination is byte-identical"
            )
        if rules.collision == "suffix" or force:
            suffixed = _suffixed(dest)
            if suffixed is None:
                return PlaceResult("conflict", src, rule.name, dest=dest, message="no free suffix")
            dest = suffixed
        else:
            return PlaceResult(
                "conflict", src, rule.name, dest=dest, message="destination exists, differs"
            )

    entry = _entry(dest, doc, rule.path_style, previous)
    if dry_run:
        return PlaceResult("ok", src, rule.name, dest=dest, entry=entry)

    try:
        _reserve(src, dest)
    except FileExistsError:
        return PlaceResult("conflict", src, rule.name, dest=dest, message="destination appeared")
    except OSError as exc:
        return PlaceResult("error", src, rule.name, dest=dest, message=str(exc))

    if rule.op in ("move", "copy"):
        if rule.op == "move":
            src.unlink()
        return PlaceResult("ok", src, rule.name, dest=dest, entry=entry)
    if rule.op == "link":  # dest is a hardlink so far; replace it with a symlink
        dest.unlink()
        dest.symlink_to(src)
    return PlaceResult("ok", src, rule.name, dest=dest, entry=entry)


def rollback(result: PlaceResult) -> None:
    """Undo a successful `place()` — call this when the `info.yaml` write fails."""
    if not result.moved or result.dest is None:
        return
    if not result.src.exists():
        _reserve(result.dest, result.src)
    result.dest.unlink(missing_ok=True)


def trash(folder: Path, trash_dir: Path) -> Path:
    """Move a document folder out of the library, recoverably.

    SPEC: *files always route through trash*, whatever `undo.strategy` is. A
    merge removes the folders it folded in, and "removed" here means "no longer
    in the library and still on disk" — nothing in ptui deletes a folder outright
    while `doc.delete` and `app.undo` do not exist.

    The destination is suffixed deterministically on collision (`_b`, `_c`, …),
    like every other name ptui reserves, so trashing the same ref twice is safe.
    """
    trash_dir = trash_dir.expanduser()
    trash_dir.mkdir(parents=True, exist_ok=True)
    dest = trash_dir / folder.name
    for suffix in ("", *(f"_{chr(c)}" for c in range(ord("b"), ord("z") + 1))):
        candidate = trash_dir / f"{folder.name}{suffix}"
        if not candidate.exists():
            dest = candidate
            break
    else:
        raise FileExistsError(f"{trash_dir} already holds every name for {folder.name}")
    shutil.move(str(folder), str(dest))
    return dest
