"""The undo history and the three strategies behind it.

SPEC: undo is a **hybrid, not a toggle**. Whatever `undo.strategy` says,
*files always route through trash* — `pdf_root` usually lives outside the
library, so git covers none of it. The strategy only decides what happens to
the metadata:

* `trash` — the document folder is moved to `undo.trash_dir`, and undo moves it
  back. Works for any library.
* `git` — the folder is `git rm`'d and committed, one commit per ptui
  operation, and undo reverts that commit. The commit log *is* the history, so
  it survives the session.
* `none` — nothing is recorded. The folder still goes to the trash, because
  that rule is not the strategy's to break; there is simply no `u` for it.

Everything here is pure enough to test without an app: a `Step` is two
callables and a label, and the git helpers take a path.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

COMMIT_PREFIX = "ptui: "
"""What marks a commit as ours. `app.undo` reverts the last commit carrying it
and refuses anything else — reverting a commit the user wrote by hand would be
a far bigger surprise than doing nothing."""


@dataclass(frozen=True, slots=True)
class Step:
    """One reversible ptui operation, as the user would describe it."""

    label: str
    undo: Callable[[], None]
    redo: Callable[[], None]


@dataclass(slots=True)
class History:
    """Bounded undo/redo stacks. One operation, not one document, is a step."""

    size: int = 50
    done: list[Step] = field(default_factory=list)
    undone: list[Step] = field(default_factory=list)

    def push(self, step: Step) -> None:
        self.done.append(step)
        del self.done[: max(0, len(self.done) - self.size)]
        self.undone.clear()  # a new operation invalidates the redo branch

    def clear(self) -> None:
        """Forget everything — `strategy = "none"` after an operation nobody
        can reverse, so `u` cannot offer the *previous* one by mistake."""
        self.done.clear()
        self.undone.clear()

    def undo(self) -> Step | None:
        if not self.done:
            return None
        step = self.done.pop()
        step.undo()
        self.undone.append(step)
        return step

    def redo(self) -> Step | None:
        if not self.undone:
            return None
        step = self.undone.pop()
        step.redo()
        self.done.append(step)
        return step


# ── trash ───────────────────────────────────────────────────────────────────


def restore(moved: list[tuple[Path, Path]]) -> list[Path]:
    """Move trashed paths back where they came from. Returns what was restored.

    A destination that has reappeared in the meantime is left alone rather than
    overwritten: undo may not clobber whatever took the name back.
    """
    back = []
    for source, dest in moved:
        if not dest.exists() or source.exists():
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(source))
        back.append(source)
    return back


# ── git ─────────────────────────────────────────────────────────────────────


def git(root: Path, *args: str) -> str:
    """Run git in `root`, raising with git's own message on failure."""
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError((done.stderr or done.stdout).strip())
    return done.stdout.strip()


def git_root(path: Path) -> Path | None:
    """The work tree `path` belongs to, or None when it is not in one."""
    try:
        return Path(git(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel"))
    except (RuntimeError, OSError):
        return None


def git_delete(root: Path, folders: list[Path], message: str) -> str:
    """`git rm -r` every folder and commit once. Returns the commit hash.

    One ptui operation is one commit (SPEC), which is what makes the log a
    usable history: reverting one entry undoes what the user did, not what it
    happened to do to the seventh document.
    """
    for folder in folders:
        git(root, "rm", "-r", "-q", "-f", str(folder))
    git(root, "commit", "-q", "-m", f"{COMMIT_PREFIX}{message}")
    return git(root, "rev-parse", "HEAD")


def git_revert(root: Path, commit: str) -> None:
    """Revert one ptui commit, keeping it in the log.

    `revert`, never `reset --hard`: the working tree may hold edits ptui knows
    nothing about, and rewriting history under a user who also uses this repo
    by hand is not undo, it is data loss.
    """
    subject = git(root, "log", "-1", "--format=%s", commit)
    if not subject.startswith(COMMIT_PREFIX):
        raise RuntimeError(f"{commit[:8]} is not a ptui commit: {subject}")
    git(root, "revert", "--no-edit", "--no-commit", commit)
    git(root, "commit", "-q", "-m", f"{COMMIT_PREFIX}undo {subject[len(COMMIT_PREFIX) :]}")


def git_tracks(root: Path, path: Path) -> bool:
    """Whether git actually has this path — a gitignored PDF is not an undo."""
    try:
        return bool(git(root, "ls-files", "--error-unmatch", str(path)))
    except RuntimeError:
        return False
