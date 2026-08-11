# TODO — backlog from real use

Feedback from the first session with a real library (2026-07-28). Ordered
roughly by how much it hurts. Root causes are noted where they are already
understood; they were traced in the code, not guessed.

## A. Broken

- **The log pane cannot be escaped** (reported 2026-07-30). `escape` in any mode
  but the list is handled above the keymap (`app.on_key`): it returns focus to
  the list but leaves the pane displayed, so the log stays on screen and escape
  looks dead. Closing it needs `g o` / `4` again — `app.log` is the only thing
  that touches `display`. Every other pane is toggled, not closed, so escape
  probably has to close whatever transient pane it is leaving.

## B. Bound but not implemented (they log "not implemented yet")

- `:` `cmdline.open` — pressing it does nothing. This is the layer that teaches
  the keymap (fuzzy completion over command names with the bound key beside
  each), so it matters more than the rest of this list.
  Only once ours works: `ctrl+p` currently opens _Textual's_ built-in command
  palette (a system binding that runs before `App.on_key`), whose "show keys and
  help panel" item opens an unstyled side pane with no obvious way out. Turn it
  off with `ENABLE_COMMAND_PALETTE = False` when ours replaces it — not before.
- `e` `doc.edit` — see C1; `E` (`doc.edit_raw`) works.
- `c t` / `c T` / `c s` / `c r` / `c f` — `doc.tag`, `doc.untag`, `doc.status`,
  `doc.rating`, `doc.set`. None of the `c` namespace exists yet. ct and cT should probably be one operation to just edit the tags...
- `d d` `doc.delete` — and therefore `u` `app.undo` cannot be tested at all.
- ~~doctor: `doctor.run`, `doctor.fix`, `doctor.fix_pick`.~~ Done (2026-07-30),
  in `doctor.py` — see § B2 for the shape it ended up with and what it replaced.
- `g n` `doc.notes`, `g s` `view.saved`, `\ s` `query.save`, `\ t` `theme.picker`.
- `f a` `files.attach`, `f n` `files.normalize`, and the `[modes.files]` verbs
  (`files.rename`, `files.repoint`, `files.detach`, `files.reorder`) — there is
  no files pane yet.

## ~~B2. Doctor is shipped broken~~ — done (2026-07-30)

The phantom findings, the picker-shaped report and the wrong fix counts are all
gone. What landed, and what was wrong in this section before it did:

1. **Only `duplicated-keys` is stateful.** Measured against papis 0.15: it is the
   single registered check with module state (`DUPLICATED_KEYS_SEEN`). This
   section claimed `duplicated-values` was "built the same way" — it is not, it
   looks for repeats *inside* one list field and is per-document. Fixed by
   `doctor.LIBRARY_WIDE`: `findings()` excludes that set, `scan_library()` is its
   one pass over the whole target set and resets papis's state first.
2. **The report is the list, not a picker.** `doctor.run` narrows to the
   documents that have findings (`app.doctor_only`, the same shape as
   `marked_only`), and the info pane shows the current document's findings as a
   section below its files. `view.doctor` is deleted. Fixing is a verb —
   `doctor.fix`, or `doctor.fix_pick` to choose one finding.
3. **Findings are cached** by `papis_id` and stamped with `info.yaml`'s mtime, so
   a written document reads *not checked* rather than showing pre-write findings.
   A thread worker fills the cache at startup (`[doctor] scan_on_startup`).
4. **Keys moved out of `f`** — doctor is not a file operation. The `!` namespace
   is doctor: `! !` scan + narrow, `! d` re-check this document, `! f` fix here,
   `! o` fix one finding, `! a` fix the marked/shown set. `g d`, `f d`, `f f`,
   `\ d` and `\ D` are gone.

Still open: **re-measure the real library.** The old "0 findings over 754
documents" number was taken with the broken per-document pass and is worthless.
Re-run per check now that the sets are split, and run the library-wide pass once
over the whole library — nothing has ever exercised `duplicated-keys` honestly.

## C. Decisions that change the defaults (and so SPEC)

1. **`edit.mode = "editor"` becomes the default**, i.e. `e` and `E` both open
   `$EDITOR` on `info.yaml`. On return, re-parse the file and report clearly if
   it is no longer valid YAML (papis will have refused to load it). The
   structured editor stays on the roadmap but is not the default.
2. ~~**`venue` belongs in the info pane** field list.~~ Done — the list is
   `app.INFO_FIELDS`, which also gained `notes`. Every entry needs a matching
   `field.<name>` glyph.

3. **`ui.icons = "auto"` — low priority.** Detect at startup whether the
   terminal can actually render the nerd-font column, and fall back to ASCII
   instead of drawing tofu. Only worth doing if the detection is honest:
   - There is no way to _ask_ a terminal what its font contains. The one real
     probe is to print a glyph, query the cursor position (CPR, `ESC[6n`), and
     see whether it advanced 1 cell or 2 — that catches double-width fallback,
     but a missing glyph rendered as a 1-cell box passes the test.
   - It also needs the raw tty before Textual takes it, so it belongs in
     `cli.py` ahead of `PtuiApp`, not in the app.
   - Everything cheaper is a guess at the environment (`$TERM_PROGRAM`,
     `$TERM`, the SSH variables) and will be wrong for someone.
     So: three values (`true` / `false` / `"auto"`), `auto` runs the CPR probe and
     loses to ASCII on any doubt, and `config.get("ui.icons")` stops being a bool
     — `ui.use_icons()` is the only caller, so that stays a one-line change.

## D. Display polish

- **Warning glyph on documents with doctor findings** — unblocked: § B2 landed
  and `doctor.CACHE` is exactly the background-pass-plus-cache this asked for, so
  the column only has to read `doctor.cached(doc)`. As the first "letter" of
  the title cell, so a broken document is visible without running anything.
  Verified against the installed papis (0.15):
  - The read-only entry point is `doctor.REGISTERED_CHECKS[name].operate(doc)`,
    which yields errors. **Never `doctor.run()`** — it defaults to `fix=True`
    and mutates the document, straight through golden rule 4. Drawing a warning
    glyph must not be able to change a single byte on disk; see § B.
  - 14 registered checks. All of them over the real library take **~1.7 s for
    754 documents**, so this cannot run inside `refresh_rows`. It needs a
    background pass that caches findings by `papis_id` and is invalidated by
    `safewrite` and by `place()`.
  - Measured on the real library: **0 findings** over the first 200 docs with
    all 14 checks. The marker is blank on essentially every row today, which is
    the right shape for an exception marker — it costs nothing until it matters.
  - `files_check` returns nothing for a document with no main folder, so a
    synthetic doc is not a valid test of it.
  - Which checks run should be configurable (`[doctor] checks`), because
    `keys-missing` on a personal library is opinionated.
    This shares the exception-marker cell with the missing-file and multi-file
    ideas below — decide the whole cell at once, not one flag at a time.
    Needs `view.doctor` / `doctor.run` from § B to be worth much.

- ~~**`list.row_height` (lines per document), default 1.**~~ Done (2026-07-30).
  At 2 the **flexible column wraps** and every other column stays on line 1, so
  `Tags` keeps its own column beside a two-line title. `library.fit_lines` does
  the wrap itself — greedy, by cells, newline-joined — rather than handing a long
  string to the widget: a `width * lines` budget fits more than a word-wrap can,
  so the ellipsis would have lied. Every row is cut to the column width, which a
  single word wider than the column turned out to need.
  Still not the default: it halves the visible documents, and the `auto` layout
  now gives the title 66+ cells on most terminals, which was the original
  argument for wrapping. `scripts/shot.py --rows 2` shows it.

- **Column set — the last open layout decision.** The rules it was waiting on
  have landed, and they took most of the problem with them: `Tags` renders
  joined (not a Python list), is `optional = true` so it never squeezes `Title`,
  and the `auto` layout keeps the list pane wide enough that it usually fits.
  Measured on the shipped columns, `Tags` now appears at ~100 cells stacked and
  ~180 side by side, and is dropped in between.
  What is still undecided is only whether a 20-cell column of _joined tag names_
  is the best use of that width. Alternatives already mocked up against the real
  library, and still open: 2-3 char codes derived from the tag vocabulary (23
  distinct tags, so they are learnable), one curated glyph per tag, or dropping
  the column and leaving tags to the info pane. Not worth building any of them
  until the current behaviour has been used for a while.

## D2. Data model — wrong assumptions to correct

1. ~~**Merge mode, over the marked documents.**~~ Done (2026-07-30), `m m`,
   in `merge.py` plus `actions.doc_merge`. The `ref` you keep is the document you
   keep — one question settles the survivor, its folder and its `papis_id`. Gaps
   are filled silently, real clashes get a picker each, and every picker offers
   _keep everything else from this document_ to end the questions. `files` is
   unioned and routed through `place()`; `time-added` becomes the earliest of the
   group; the folded-in folders go to `undo.trash_dir`.
   Learned while building it, all now commented in the code:
   - **Trashing a folder is not enough.** papis keeps its own index and hands the
     document straight back on reload; `papis.database.delete` has to be called
     too, which is exactly what `papis rm` does.
   - A loser's `files` entry may be **relative to the folder about to be
     trashed**, so it is resolved before the move, not after.
   - The `app` test fixture now redirects `undo.trash_dir` into `tmp_path`. Until
     it did, this test quietly filled the real `~/.local/share/ptui/trash`.
   - **Measured: 0 duplicates in the real library** by title, DOI, eprint or ref,
     so this is exercised by fixtures rather than by real data.
     Still open: nothing finds duplicates for you. papis's `duplicated-keys` /
     `duplicated-values` doctor checks and a title-similarity pass are the
     candidates, and both want measuring before being trusted.

2. **`preprint` misses arXiv entries that have no DOI at all.** `library.kind()`
   only asks whether `doi` starts with `10.48550/arxiv`, so an arXiv import that
   never recorded a DOI reads as `article`. Widen it: arXiv DOI, **or** — only
   when the document has no `doi` — an arxiv.org `url` (`eprint` too, if it is
   as common). A non-arXiv DOI still means published, so the URL fallback must
   never override a real DOI. Measure both counts against the real library
   before changing the default.

## E. New features asked for

1. **"Add to"** — attach a file to an _existing_ document (`files.attach`,
   already in the registry and bound to `f a`), routed through `place()`.

## F. Packaging

- **Not on PyPI**, so `uv tool install ptui` / `pipx install ptui` do not exist.
  The README said they did; it now documents the checkout install instead.
  Publish once § A is clear and the thing is worth other people's time.
- **`papis ptui` only works inside the environment that holds ptui.** The
  `papis.command` entry point is resolved through papis's own metadata, so a
  pipx-installed papis cannot see a uv-installed ptui. Documented in the README
  (`pipx inject --editable --include-apps papis <path>`); nothing to fix in the
  code, but expect this question from every user who has papis from pipx or the
  distro.

## G. Seeing the UI without a terminal (done — use it)

`scripts/shot.py` boots the app headlessly against the real library, presses a
key sequence, and writes a PNG of the resulting screen:

```sh
uv run python scripts/shot.py /tmp/shot.png --size 160x30 slash n a u e n
uv run python scripts/shot.py --text --size 120x20 j space     # plain text
```

Every visual claim in this file should be checked that way before and after a
fix. Snapshot regressions can move to `pytest-textual-snapshot` (already a dev
dependency) once the layout stops changing every day.

**The PNG lies about spacing.** SVG export writes each styled run as its own
positioned text element, so bold text looks like it has lost the space beside it
(`enteropen file`) and marked rows look misaligned. Neither is real. Use
`--text` — the composited screen buffer — for anything about alignment, padding
or truncation, and the PNG only for layout and colour. Two entries in this file
were wrong for exactly this reason before being checked.

## Not yet exercised

Regex narrow mode, marks at scale, `lib.switch`, and the
`[modes.files]` keymap.
