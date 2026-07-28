# TODO — backlog from real use

Feedback from the first session with a real library (2026-07-28). Ordered
roughly by how much it hurts. Root causes are noted where they are already
understood; they were traced in the code, not guessed.

## A. Broken — fix first

1. **The keyboard dies after `tab` or `1`–`4`.** After switching panes, only
   `j`/`k` work; `E`, `q`, `G`, `g g` are gone. Cause: `app.mode` becomes
   `info`/`files`/`log` and each mode's keymap is used *alone* —
   `[modes.info]` defines four bindings, and `[modes.log]` does not exist at
   all, so the log pane traps the keyboard completely (this is also why the
   operations log cannot be closed again).

   **Decided fix: `escape` always leaves the current mode.** Modes stay modes —
   a mode is allowed to define few keys — but escape is guaranteed by the
   dispatcher, above the keymap, so no pane can ever trap the keyboard. In the
   list mode escape keeps its `escape_chain` meaning. Not doing the alternative
   (falling back to `[modes.list]` for undefined chords).

2. **`z z` (vertical ↔ horizontal split) does nothing visible.** Cause:
   `pane.toggle_layout` flips `styles.layout` on `#panes`, but the list pane
   keeps its explicit `width: 45%`, so the panes stay put. Fix: swap width/height
   constraints along with the layout.

3. **Hiding the info pane leaves the list at its old width.** The list must
   expand to the full window when it is the only pane.

4. **The list overflows horizontally.** Columns must fit the pane: the flex
   column absorbs the remainder and cells truncate (with `wcwidth`, per SPEC
   § "Terminal reality"). No horizontal scrolling.

5. **`/` narrows far too little.** `Nauen` returns obviously unrelated entries —
   measured on the real library: **695 of 747 documents still shown**. Cause:
   fuzzy narrowing is a subsequence test over *all* narrow fields joined into
   one string, so scattered letters match. Fix: make `substring` the default
   `query.narrow_mode`, and make fuzzy match per field with contiguity
   preferred (rank, don't just filter).

## B. Bound but not implemented (they log "not implemented yet")

- `?` `help.show` — the generated help overlay. SPEC § "Discoverability" layer 3.
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
2. **The list pane is the larger one** in the side-by-side layout —
   `ui.split_ratio` should favour the list (~0.6+), not 0.45.
3. **The list pane can never be hidden.** Drop `pane.toggle` for `list`; only
   the info, files and log panes toggle.
4. **`venue` belongs in the info pane** field list.

## D. Display polish

- `tags` (and any other list value) renders as a Python list —
  `['vision-transformers']`. Join lists for display, in the info pane and in
  list columns.
- **The hint bar and which-key panel lose the space between key and
  description**: `enteropen file`, `ddoctor findings`. Seen in a screenshot
  (§ F), not reported — the markup `[bold]{keys}[/] {desc}` is not surviving
  rendering. Probably needs an explicit separator instead of a plain space.
- Marked rows shift right: the `*` glyph is prepended inside the first cell, so
  a bold marked row no longer lines up with its neighbours. Put the mark in its
  own fixed-width column.

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

## F. Seeing the UI without a terminal (done — use it)

`scripts/shot.py` boots the app headlessly against the real library, presses a
key sequence, and writes a PNG of the resulting screen:

```sh
uv run python scripts/shot.py /tmp/shot.png --size 160x30 slash n a u e n
```

Every visual claim in this file should be checked that way before and after a
fix. Snapshot regressions can move to `pytest-textual-snapshot` (already a dev
dependency) once the layout stops changing every day.

## Not yet exercised

Narrow modes other than the default, marks at scale, `lib.switch`, and the
`[modes.files]` keymap.
