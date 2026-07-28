"""The only path by which ptui writes `info.yaml`.

Papis has no locking, so two ptui instances — or ptui plus a user script — will
interleave eventually. The defence is: remember the mtime we read, re-check it
before writing, never merge silently, and swap the file in atomically.

Unknown keys, key order and comments survive a write (ruamel round-trip); the
user's external scripts add keys ptui has never heard of.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import papis.database
from papis.document import Document
from ruamel.yaml import YAML

_yaml = YAML()  # round-trip mode: preserves comments, order and quoting
_yaml.preserve_quotes = True
_yaml.width = 4096  # papis writes long lines; rewrapping them is a noisy diff


class StaleError(Exception):
    """`info.yaml` changed under us. Reload before retrying; never merge blind."""


@dataclass(slots=True)
class InfoFile:
    path: Path
    data: Any
    """A ruamel CommentedMap — mutate it in place."""
    mtime_ns: int


def read(path: Path) -> InfoFile:
    mtime_ns = path.stat().st_mtime_ns
    return InfoFile(path, _yaml.load(path) or {}, mtime_ns)


def write(info: InfoFile) -> None:
    """Atomically replace `info.yaml`, refusing if it changed since `read`."""
    if info.path.stat().st_mtime_ns != info.mtime_ns:
        raise StaleError(f"{info.path} changed on disk since it was read")

    tmp = info.path.with_name(f"{info.path.name}.ptui.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fd:
            _yaml.dump(info.data, fd)
            fd.flush()
            os.fsync(fd.fileno())
        os.replace(tmp, info.path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    info.mtime_ns = info.path.stat().st_mtime_ns


def edit(doc: Document, mutate: Callable[[Any], None]) -> None:
    """Safely apply `mutate` to a document's `info.yaml` and resync papis.

    Raises `StaleError` if the file moved under us — the caller reloads and
    tells the user. Nothing is written in that case.
    """
    info = read(Path(doc.get_info_file()))
    mutate(info.data)
    write(info)
    doc.load()
    papis.database.get().update(doc)
