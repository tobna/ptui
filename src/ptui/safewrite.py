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
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark
from ruamel.yaml.tokens import CommentToken


def _yaml() -> YAML:
    """A fresh instance per load and per dump: ruamel keeps state on the instance
    and clears none of it. A dump raising left its output and context there, so
    a shared one sent every later dump to the closed tmp file ("I/O operation on
    closed file" until a restart); and every load appends a `DocInfo` to
    `doc_infos`, which grew for the whole session."""
    yaml = YAML()  # round-trip mode: preserves comments, order and quoting
    yaml.preserve_quotes = True
    yaml.width = 4096  # papis writes long lines; rewrapping them is a noisy diff
    return yaml


class StaleError(Exception):
    """`info.yaml` changed under us. Reload before retrying; never merge blind."""

    done = 0
    """Raised from an undo or a redo: on how many other documents it did happen."""


@dataclass(slots=True)
class InfoFile:
    path: Path
    data: Any
    """A ruamel CommentedMap — mutate it in place."""
    mtime_ns: int


def read(path: Path) -> InfoFile:
    mtime_ns = path.stat().st_mtime_ns
    return InfoFile(path, _yaml().load(path) or {}, mtime_ns)


def write(info: InfoFile) -> None:
    """Atomically replace `info.yaml`, refusing if it changed since `read`."""
    if info.path.stat().st_mtime_ns != info.mtime_ns:
        raise StaleError(f"{info.path} changed on disk since it was read")

    tmp = info.path.with_name(f"{info.path.name}.ptui.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fd:
            _yaml().dump(info.data, fd)
            fd.flush()
            os.fsync(fd.fileno())
        os.replace(tmp, info.path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    info.mtime_ns = info.path.stat().st_mtime_ns


def _tail(node: Any, key: Any) -> tuple[list[Any], int] | None:
    """The comment slot after `node[key]`'s value, when it holds a comment.

    ruamel hangs a comment on the last node *before* it: the map entry for a
    scalar, the last item for a list — recursively, so a list of maps keeps it
    on the last key of the last map.
    """
    value = node[key]
    if isinstance(value, CommentedMap) and value:
        return _tail(value, next(reversed(value)))
    if isinstance(value, CommentedSeq) and value:
        return _tail(value, len(value) - 1)
    slot = node.ca.items.get(key)
    index = 0 if isinstance(node, CommentedSeq) else 2
    return (slot, index) if slot and slot[index] else None


def _detach(node: Any, key: Any) -> str:
    """Take the full-line comments trailing `node[key]` off it. The end-of-line
    comment is about the value itself, so it stays and goes with the value."""
    tail = _tail(node, key)
    if tail is None:
        return ""
    slot, index = tail
    eol, _, rest = slot[index].value.partition("\n")
    if rest:
        slot[index] = CommentToken(f"{eol}\n", CommentMark(0)) if eol else None
    return rest


def _hang(node: Any, key: Any, text: str) -> None:
    """Hang full-line comments after `node[key]`'s value, wherever ruamel looks."""
    value = node[key]
    if isinstance(value, dict) and value:
        if not isinstance(value, CommentedMap):
            value = node[key] = CommentedMap(value)
        _hang(value, next(reversed(value)), text)
        return
    if isinstance(value, list) and value:
        if not isinstance(value, CommentedSeq):
            value = node[key] = CommentedSeq(value)
        _hang(value, len(value) - 1, text)
        return
    slot = node.ca.items.setdefault(key, [None, None, None, None])
    index = 0 if isinstance(node, CommentedSeq) else 2
    old = slot[index].value.rstrip("\n") if slot[index] else ""
    slot[index] = CommentToken(f"{old}\n{text}", CommentMark(0))


def put(data: Any, key: str, value: Any) -> None:
    """`data[key] = value`, keeping the comment lines that followed the old value.

    Replacing a list replaces the node ruamel hung the next comment on, so a bare
    assignment silently deletes a comment that belongs to the key *after* it.
    """
    text = _detach(data, key) if key in data else ""
    data[key] = value
    if text:
        _hang(data, key, text)


def plain(node: Any) -> Any:
    """A value read from `info.yaml`, its maps and lists rebuilt without ruamel's
    comment slots. Kept for undo, the node itself would carry them into the next
    file, and `put` hangs the comment that follows onto it again on every write.
    """
    if isinstance(node, dict):
        return {key: plain(value) for key, value in node.items()}
    if isinstance(node, list):
        return [plain(item) for item in node]
    return node


def drop(data: Any, key: str) -> None:
    """`data.pop(key)`, moving the comment lines that followed it onto the key
    before — they introduce whatever comes next, not the key being removed."""
    if key not in data:
        return
    keys = list(data)
    at = keys.index(key)
    text = _detach(data, key)
    data.pop(key)
    if not text:
        return
    if at:
        _hang(data, keys[at - 1], text)
    elif len(keys) > 1:  # the first key: the comment now opens the map
        data.ca.items.setdefault(keys[1], [None, [], None, None])[1] = [CommentToken(text, CommentMark(0))]


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
