---
description: Bring CLAUDE.md and README.md back in sync with the code
---

Check `CLAUDE.md` and `README.md` against the current state of the repo and fix
whatever drifted. Do not rewrite what is still correct.

1. `git diff HEAD~5..HEAD --stat` and `ls -R src` — what changed recently?
2. `CLAUDE.md` (for agents): layout, toolchain commands, status/what's built,
   conventions, papis API gotchas. Must be enough for a cold agent to work here.
   Drop anything no longer true.
3. `README.md` (for humans): short, GitHub-pretty, install + a feature list +
   config pointers. No internals, no changelog.
4. If behaviour diverged from `SPEC.md`, update `SPEC.md` too and say why.

Report in one line per file what you changed, or "no drift".
