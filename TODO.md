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
- `g d` / `\ d` `view.doctor`, `doctor.run`, `doctor.fix`.
- `g n` `doc.notes`, `g s` `view.saved`, `\ s` `query.save`, `\ t` `theme.picker`.
- `f a` `files.attach`, `f n` `files.normalize`, and the `[modes.files]` verbs
  (`files.rename`, `files.repoint`, `files.detach`, `files.reorder`) — there is
  no files pane yet.

## C. Decisions that change the defaults (and so SPEC)

1. **`edit.mode = "editor"` becomes the default**, i.e. `e` and `E` both open
   `$EDITOR` on `info.yaml`. On return, re-parse the file and report clearly if
   it is no longer valid YAML (papis will have refused to load it). The
   structured editor stays on the roadmap but is not the default.
2. **`venue` belongs in the info pane** field list.
3. **`ui.layout` gains `"auto"`, and it becomes the default.** Three values:
   `vertical` (split `|`, panes side by side), `horizontal` (split `-`, info
   under the list), and `auto` — pick from the terminal width, side by side
   only when the list pane would still be wide enough to be worth it, stacked
   below that. This is the same idea as the unbuilt `ui.narrow_width` collapse
   in SPEC § "Terminal reality"; fold the two together rather than shipping two
   thresholds. `z z` keeps overriding by hand for the session.
3. **`[ui] icons` must actually mean something.** The setting exists and is
   honoured in exactly one place (the mark glyph, `app.py`); every other symbol
   is hardcoded ASCII (`·`/`!` for file present/missing, `↑`/`↓` for sort, `>`
   in the picker, pane borders). Define one glyph table with an ASCII and a
   nerd-font column, look every symbol up through it, and never emit a glyph
   directly. Shipped default stays `false` — the HPC/SSH case in SPEC
   § "Terminal reality" is real — but the maintainer runs `icons = true`, so
   the nerd-font column is the one that has to look good, not an afterthought.
   Worth a screenshot in both modes (§ G) once it lands.

## D. Display polish

- ~~`tags` renders as a Python list~~ — done: `library.display` joins lists and
  `library.flatten` runs before every column format.
- ~~Size fixed columns to the p90 of the current selection~~ — done:
  `PtuiApp.natural_width`. `Author` went 18 → 9 cells on the real library.
- ~~Cut titles at a word boundary~~ — done: `library.fit` backs off to the last
  colon, else the last space, unless that wastes over 40% of the budget.

- **`list.row_height` (lines per document), default 1.** At 2, the title wraps
  over both lines and tags get room — worth it because titles are 66 cells at
  the median and 95 at p90, and only 12% fit the ~38 cells the flex column gets
  in a side-by-side split. Costs half the visible documents, so it stays a
  setting, not a default.

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
