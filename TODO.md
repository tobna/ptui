# TODO — backlog from real use

Feedback from the first session with a real library (2026-07-28). Ordered
roughly by how much it hurts. Root causes are noted where they are already
understood; they were traced in the code, not guessed.

## A. Broken — fix first

1. **`/` narrows far too little.** `Nauen` returns obviously unrelated entries —
   measured on the real library: **695 of 747 documents still shown**. Cause:
   fuzzy narrowing is a subsequence test over *all* narrow fields joined into
   one string, so scattered letters match. Fix: make `substring` the default
   `query.narrow_mode`, and make fuzzy match per field with contiguity
   preferred (rank, don't just filter).

2. **The filter box in every picker does nothing.** Measured with `f o` on a
   document that has both `… Vision Transformer.pdf` and
   `… Vision Transformer_note.pdf`: typing `note` still shows both. Cause: the
   same one as A1 — `ui.Item.matches` runs `library.is_subsequence` over
   `label + hint + haystack`, and a long file name contains almost any needle
   as a scattered subsequence (`Locality-Atte*n*ding visi*o*n *T*ransform*e*r`).
   `o` picks the right file; only the picker's filter is broken.
   Fix once, in the matcher both layers share: substring by default, fuzzy
   ranked by contiguity. Then `f o`, `S`, `g l` and `/` all improve together.

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
- `g d` / `\ d` `view.doctor`, `doctor.run`, `doctor.fix`. **`doctor.run` must
  never fix anything** — it reports, and `doctor.fix` applies one finding that
  the user selected. papis makes this easy to get wrong: `papis.commands.doctor.
  run(doc, checks)` takes `fix=True` *by default* and mutates the document, so
  our `doctor.run` must call `REGISTERED_CHECKS[name].operate(doc)` per check
  instead, and any fix must land through `safewrite` like every other write.
  Same rule for `add.auto_doctor` (`actions.py:457`, already defaults to false).
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
3. **`ui.layout` gains `"auto"`, and it becomes the default.** Three values:
   `vertical` (split `|`, panes side by side), `horizontal` (split `-`, info
   under the list), and `auto` — pick from the terminal width, side by side
   only when the list pane would still be wide enough to be worth it, stacked
   below that. This is the same idea as the unbuilt `ui.narrow_width` collapse
   in SPEC § "Terminal reality"; fold the two together rather than shipping two
   thresholds. `z z` keeps overriding by hand for the session.
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

- **`list.row_height` (lines per document), default 1.** At 2, the title wraps
  over both lines and tags get room — worth it because titles are 66 cells at
  the median and 95 at p90, and only 12% fit the ~38 cells the flex column gets
  in a side-by-side split. Costs half the visible documents, so it stays a
  setting, not a default.

- ~~**A preprint icon.**~~ Done: `library.kind()` returns `preprint` for an
  article whose only DOI is arXiv's and which names no journal, booktitle or
  venue. Local data only, by decision. On the real library that is **181 of
  754** documents. Known limits, all measured (2026-07-30):
  - It flags *Sanity Checks for Saliency Maps*, NeurIPS 2018 — the entry was
    imported from arXiv and never refreshed, and no local field distinguishes
    that from a genuine preprint. The year histogram shows the shape of the
    error: 44 of the 181 are from 2013-2022 and are mostly stale, 107 are from
    2024-2026 and are mostly right.
  - 25 further docs have **no DOI at all** and no venue. The rule requires an
    arXiv DOI, so they stay `article`. Loosening it to "arXiv DOI *or* none"
    would take the count to 206; not done, because absence of a DOI is much
    weaker evidence than arXiv having minted one.
  - An **age guard** would be the cheapest accuracy win and needs no network:
    only call it a preprint within a year or two of `year`.
  - Settling it properly needs a network lookup, and only one source works.
    arXiv's own `journal_ref` is **empty** for that NeurIPS paper (authors
    rarely update it), and `papis.arxiv.get_data(id_list=...)` discards
    `journal_ref` and `doi` anyway — it returns only what `arxiv_to_papis` maps.
    OpenAlex answers it: `api.openalex.org/works?filter=doi:<doi>` returns
    `locations[].source.display_name`, which for that paper is
    `[arXiv, arXiv, Neural Information Processing Systems]`. Preprint-only means
    every location is a preprint server. Free, no key, wants a `mailto`. Use
    `locations` and **not** the top-level `type`, which says `preprint` even
    there because it describes the record queried. The 181 local hits are the
    only ones worth asking about, and the answers belong in the same
    `papis_id`-keyed side cache the doctor glyph needs — build one, not two.

- **Optional columns must earn their width.** Today a fixed column is dropped
  only when it would push the flex column below `MIN_FLEX = 12`, which means
  `Tags` can survive while `Title` is squeezed to 12 cells — the wrong trade.
  Give the flex column a *target* width (~45) that optional columns are
  allocated after, so `Tags` appears only once the title is comfortable. A
  per-column `optional = true` (or a priority) in `[[list.columns]]` is
  probably the shape.

- Column set: `Tags` renders as a Python list and only survives on a wide
  terminal. Decide what earns the space once the rules above land.

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

Narrow modes other than the default, marks at scale, `lib.switch`, and the
`[modes.files]` keymap.
