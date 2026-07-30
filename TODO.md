# TODO — backlog from real use

Feedback from the first session with a real library (2026-07-28). Ordered
roughly by how much it hurts. Root causes are noted where they are already
understood; they were traced in the code, not guessed.

## A. Broken — fix first

1. ~~**`/` narrows far too little.**~~ Done (2026-07-30). `library.parse_query`
   splits the query into whitespace-separated terms and `match_doc` ANDs them, so
   typing more always narrows. `nauen` went from **702 of 754** to **18**;
   `coreset` from 496 to 25. Substring is the shipped `narrow_mode`; fuzzy is
   opt-in and now requires the matched run to stay within `FUZZY_SPAN` times the
   needle, which is what killed the old whole-query subsequence test. Grammar:
   bare terms, `-negation`, `"quoted phrases"`, `field:value` through the same
   `[query.aliases]` the scope prompt uses, and `year:>2023` / `year:2020..2024`
   ranges. 2 ms per keystroke over the whole library.
   Deliberately **not** ranked: with terms ANDed the result set is already small,
   and re-ordering would override the sort the user picked, which SPEC keeps
   independent of narrowing.

2. ~~**The filter box in every picker does nothing.**~~ Done, by the same change:
   `ui.Item.matches` now calls `library.match_text` with `library.parse_query`,
   so `f o`, `S`, `g l`, `?` and `/` share one matcher. `note` no longer matches
   `Locality-Attending Vision Transformer.pdf`; there is a test for exactly that.

## B. Bound but not implemented (they log "not implemented yet")

- `:` `cmdline.open` — pressing it does nothing. This is the layer that teaches
  the keymap (fuzzy completion over command names with the bound key beside
  each), so it matters more than the rest of this list.
  Only once ours works: `ctrl+p` currently opens *Textual's* built-in command
  palette (a system binding that runs before `App.on_key`), whose "show keys and
  help panel" item opens an unstyled side pane with no obvious way out. Turn it
  off with `ENABLE_COMMAND_PALETTE = False` when ours replaces it — not before.
- `e` `doc.edit` — see C1; `E` (`doc.edit_raw`) works.
- `c t` / `c T` / `c s` / `c r` / `c f` — `doc.tag`, `doc.untag`, `doc.status`,
  `doc.rating`, `doc.set`. None of the `c` namespace exists yet.
- `d d` `doc.delete` — and therefore `u` `app.undo` cannot be tested at all.
- ~~`g d` / `\ d` `view.doctor`, `doctor.run`, `doctor.fix`.~~ Done
  (2026-07-30), in `doctor.py`. `\ d` reports into the log, `g d` browses
  findings and `enter` fixes that one, `\ D` fixes every fixable finding on the
  target set. Nothing writes unless asked. `doctor.run` never calls papis's
  `doctor.run` — that defaults to `fix=True`. `[doctor] checks = []` = all.
  **Measured: 0 findings across all 14 checks over all 754 documents**, so the
  UI is exercised by a deliberately broken fixture document, not by the library.
  `doctor.fix` deviates from SPEC's "one selected finding": a `Finding` cannot
  travel through a `keys.toml` argument, so the by-name command is the batch one
  and the single-finding path is `view.doctor`'s `enter`. SPEC updated to match.
- `g n` `doc.notes`, `g s` `view.saved`, `\ s` `query.save`, `\ t` `theme.picker`.
- `f a` `files.attach`, `f n` `files.normalize`, and the `[modes.files]` verbs
  (`files.rename`, `files.repoint`, `files.detach`, `files.reorder`) — there is
  no files pane yet.

## C. Decisions that change the defaults (and so SPEC)

1. **`edit.mode = "editor"` becomes the default**, i.e. `e` and `E` both open
   `$EDITOR` on `info.yaml`. On return, re-parse the file and report clearly if
   it is no longer valid YAML (papis will have refused to load it). The
   structured editor stays on the roadmap but is not the default.
2. ~~**`venue` belongs in the info pane** field list.~~ Done — the list is
   `app.INFO_FIELDS`, which also gained `notes`. Every entry needs a matching
   `field.<name>` glyph.
3. ~~**`ui.layout` gains `"auto"`, and it becomes the default.**~~ Done
   (2026-07-30). Two states, one threshold, and the threshold is a *column*
   target rather than a terminal width: `auto` goes side by side only while the
   flexible column would still reach `list.flex_target` (45) in the narrower
   pane, so adding a column moves the threshold on its own. On the shipped
   columns that is ~160 cells. Measured, why 45: titles here are 66 cells at the
   median, and side by side does not give the title column 66 until the terminal
   is ~198 wide, while stacked reaches it at ~118.
   `ui.narrow_width` and its single-pane collapse are **dropped** — the list pane
   can never be hidden, so the third state added a threshold without adding a
   capability, and `z i` already hides the info pane. `z z` clears
   `app.layout_auto` for the session.

3. ~~**`[ui] icons` must actually mean something.**~~ Done: `ui.GLYPHS` is the
   one table (ASCII + nerd font), reached through `ui.glyph()`; mark, file
   present/missing, sort direction and the picker cursor all go through it.
   `scripts/shot.py --icons` shows either mode. Still hardcoded and *not* in the
   table: pane borders, which are Textual CSS (`border: round`) and not a font
   question. Open: the nerd column was chosen from the Font Awesome range
   without being seen in a real terminal — swap any glyph that reads badly.
   **The shipped default is now `true`** (2026-07-30), against the original SSH
   argument: the terminal with a patched font is the common case, both columns
   are one cell wide so a wrong guess costs tofu and not a broken layout, and
   `icons = false` is one line of config. SPEC updated in the same commit.

4. **`ui.icons = "auto"` — low priority.** Detect at startup whether the
   terminal can actually render the nerd-font column, and fall back to ASCII
   instead of drawing tofu. Only worth doing if the detection is honest:
   - There is no way to *ask* a terminal what its font contains. The one real
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

- ~~`tags` renders as a Python list~~ — done: `library.display` joins lists and
  `library.flatten` runs before every column format.
- ~~Size fixed columns to the p90 of the current selection~~ — done:
  `PtuiApp.natural_width`. `Author` went 18 → 9 cells on the real library.
- ~~Cut titles at a word boundary~~ — done: `library.fit` backs off to the last
  colon, else the last space, unless that wastes over 40% of the budget.
- ~~Sort direction in the column header~~ — done: `PtuiApp.header` shows `Year ↓`
  on whichever column's `format` matches the sort key, and nothing when the key
  is not a column (`time-added`). The status bar keeps the key name, which is
  the only indicator in that case.

- **Warning glyph on documents with doctor findings**, as the first "letter" of
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

- ~~**Optional columns must earn their width.**~~ Done (2026-07-30).
  `fit_columns` allocates in two passes: required columns first, kept above
  `MIN_FLEX`, then `optional = true` columns, kept above `list.flex_target`
  (45) — the same knob the `auto` layout uses. `Tags` is the one optional column
  today. Forced side by side at 110 cells it used to survive on a 14-cell
  `Title`; now it is dropped and `Title` gets 36. On the shipped columns `Tags`
  reappears around 180 cells side by side, or 100 stacked.
  Deliberately no priority number: with one optional column, config order is
  the ordering, and a second one can have it if it ever needs a different rank.
  Found while measuring this: the layout decision ran in `on_mount` *before*
  `actions.reload`, so column widths came from an empty list and every column
  looked ~3 cells narrower than reality, flipping the layout wrongly on a
  borderline width. It now re-decides after the load and again from
  `ListTable.on_resize`, where the scrollbar and widget widths are real. Also
  fixed a crash that only appeared at the flip width: a `RowHighlighted` queued
  by the column rebuild is delivered after teardown, and `refresh_info` raised
  `NoMatches` on the way out.

- **Column set — the last open layout decision.** The rules it was waiting on
  have landed, and they took most of the problem with them: `Tags` renders
  joined (not a Python list), is `optional = true` so it never squeezes `Title`,
  and the `auto` layout keeps the list pane wide enough that it usually fits.
  Measured on the shipped columns, `Tags` now appears at ~100 cells stacked and
  ~180 side by side, and is dropped in between.
  What is still undecided is only whether a 20-cell column of *joined tag names*
  is the best use of that width. Alternatives already mocked up against the real
  library, and still open: 2-3 char codes derived from the tag vocabulary (23
  distinct tags, so they are learnable), one curated glyph per tag, or dropping
  the column and leaving tags to the info pane. Not worth building any of them
  until the current behaviour has been used for a while.

## D2. Data model — wrong assumptions to correct

1. ~~**`venue` is not a place.**~~ Done (2026-07-30). `library.venue()` returns
   the first non-empty of `booktitle`, `journal`, `journaltitle`; the `venue` key
   is never read for it, because measured values are `New Orleans, Louisiana,
   USA`, `Sydney, Australia`, `Vancouver, BC, Canada`. `kind()` uses the helper,
   and `flatten()` injects the name under `venue` so every display path gets it.
   Measured effect on the real library:
   - documents showing a venue **name**: 237 -> **524**. The old code read the
     raw key, so a third of what it displayed was a city and most published
     documents showed nothing at all.
   - `kind()` moved **10** documents from `preprint` to `article` — all of them
     genuinely published (`ICML 2024`, `JMLR`, `ICLR 2019`, `TMLR`) and only
     recorded in `journaltitle`, which the old three-key check never consulted.
   - no document in this library has a city as its *only* venue-ish field, so
     nothing moved the other way. The guard is for correctness, not a fix.

2. **Merge mode, over the marked documents.** Duplicates are the reason: the same
   paper arrives twice, once from arXiv and once from the proceedings, and the
   two records each hold fields the other lacks. Shape to work out before coding:
   - Trigger from marks (`m`-namespace, or `d`/`c`), on exactly the marked set.
   - A field-by-field picker: for every key where the marked documents disagree,
     choose which value survives; identical values need no question. `SelectList`
     already does one-of-many, so this is a loop over conflicting keys.
   - `files` **unions** rather than picks — losing an attachment is the one
     unrecoverable outcome here.
   - One surviving `papis_id`; the others' folders are left on disk, not deleted,
     until `doc.delete` exists and is trusted (§ B).
   - Every write through `safewrite`, and the whole thing dry-runnable, like
     `place()` already is.
   - Finding the duplicates is a separate job from merging them: papis has
     `doctor`'s `duplicated-keys` / `duplicated-values` checks, and a
     `ref`/`doi`/title-similarity pass would want measuring on the real library
     before being trusted.

## E. New features asked for

1. **More add sources**, in priority order:
   - from a `.bib` file (import entries, attach nothing)
   - from an arXiv ID or URL
   - from a DOI
   Each fills the same metadata form, so `[add] fetch_metadata` /
   `confirm_metadata` finally mean something. papis already has the fetchers
   (`papis.crossref`, `papis.arxiv`, `papis.bibtex`) — do not write new ones.
2. **"Add to"** — attach a file to an *existing* document (`files.attach`,
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
