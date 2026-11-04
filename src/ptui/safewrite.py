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
    """Build a fresh YAML instance for one load or one dump.

    ruamel keeps state on the instance and clears none of it. A dump raising left its output and
    context there, so a shared one sent every later dump to the closed tmp file ("I/O operation on
    closed file" until a restart); and every load appends a `DocInfo` to `doc_infos`, which grew for
    the whole session.

    Returns:
        A round-trip `YAML`, configured to keep quotes and long lines.
    """
    yaml = YAML()  # round-trip mode: preserves comments, order and quoting
    yaml.preserve_quotes = True
    yaml.width = 4096  # papis writes long lines; rewrapping them is a noisy diff
    return yaml


class StaleError(Exception):
    """`info.yaml` changed under us. Reload before retrying; never merge blind.

    Attributes:
        done: Raised from an undo or a redo: on how many other documents it did happen.
    """

    done = 0
    """Raised from an undo or a redo: on how many other documents it did happen."""


@dataclass(slots=True)
class InfoFile:
    """A parsed `info.yaml`, with the modification time it was read at.

    Attributes:
        path: The file on disk.
        data: A ruamel `CommentedMap`, mutated in place.
        mtime_ns: The file's `st_mtime_ns` when read, or after the last write.
    """

    path: Path
    data: Any
    """A ruamel CommentedMap — mutate it in place."""
    mtime_ns: int


def read(path: Path) -> InfoFile:
    """Parse an `info.yaml` and remember when it was last modified.

    Args:
        path: The file to read.

    Returns:
        The parsed file; `data` is an empty dict when the file holds nothing.
    """
    mtime_ns = path.stat().st_mtime_ns
    return InfoFile(path, _yaml().load(path) or {}, mtime_ns)


def write(info: InfoFile) -> None:
    """Atomically replace `info.yaml`, refusing if it changed since `read`.

    Args:
        info: The file as `read` returned it, with `data` mutated; `mtime_ns` is refreshed on success.

    Raises:
        StaleError: When the file changed since `read`; nothing is written then.
    """
    if info.path.stat().st_mtime_ns != info.mtime_ns:
        raise StaleError(f"{info.path} changed on disk since it was read")

    temporary = info.path.with_name(f"{info.path.name}.ptui.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            _yaml().dump(info.data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, info.path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    info.mtime_ns = info.path.stat().st_mtime_ns


def _tail(node: Any, key: Any) -> tuple[list[Any], int] | None:
    """The comment slot after `node[key]`'s value, when it holds a comment.

    ruamel hangs a comment on the last node *before* it: the map entry for a
    scalar, the last item for a list — recursively, so a list of maps keeps it
    on the last key of the last map.

    Args:
        node: A ruamel map or list.
        key: The key or index of the entry.

    Returns:
        `(slot, index)` locating the comment token in `node.ca.items`, or `None` when there is none.
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
    """Take the full-line comments trailing `node[key]` off it.

    The end-of-line comment is about the value itself, so it stays and goes with the value.

    Args:
        node: A ruamel map or list.
        key: The key or index of the entry.

    Returns:
        The comment lines removed, as text; empty when there were none.
    """
    tail = _tail(node, key)
    if tail is None:
        return ""
    slot, index = tail
    end_of_line, _, rest = slot[index].value.partition("\n")
    if rest:
        slot[index] = CommentToken(f"{end_of_line}\n", CommentMark(0)) if end_of_line else None
    return rest


def _hang(node: Any, key: Any, text: str) -> None:
    """Hang full-line comments after `node[key]`'s value, wherever ruamel looks.

    Args:
        node: A ruamel map or list.
        key: The key or index of the entry to hang them after.
        text: Comment lines, as `_detach` returned them.
    """
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

    Args:
        data: The ruamel map being edited.
        key: The key to set.
        value: Its new value.
    """
    text = _detach(data, key) if key in data else ""
    data[key] = value
    if text:
        _hang(data, key, text)


def plain(node: Any) -> Any:
    """Rebuild a value read from `info.yaml` without ruamel's comment slots.

    Kept for undo, the node itself would carry them into the next file, and `put` hangs the comment
    that follows onto it again on every write.

    Args:
        node: Any value from a ruamel document.

    Returns:
        The same value with its maps and lists rebuilt as plain `dict` and `list`.
    """
    if isinstance(node, dict):
        return {key: plain(value) for key, value in node.items()}
    if isinstance(node, list):
        return [plain(item) for item in node]
    return node


def drop(data: Any, key: str) -> None:
    """Do `data.pop(key)`, moving the comment lines that followed it onto the key before.

    They introduce whatever comes next, not the key being removed.

    Args:
        data: The ruamel map being edited.
        key: The key to remove; a key not in the map is ignored.
    """
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

    Args:
        doc: The document to change.
        mutate: Called with the ruamel map; changes it in place.

    Raises:
        StaleError: When the file moved under us; the caller reloads and tells the user. Nothing is
            written in that case.
    """
    info = read(Path(doc.get_info_file()))
    mutate(info.data)
    write(info)
    doc.load()
    papis.database.get().update(doc)
