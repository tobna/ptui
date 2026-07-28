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
   operations log cannot be closed again). Fix: `Keymap.lookup` should fall back
   to `[modes.list]` for any chord the active mode does not define, and `escape`
   must always return to the list.

2. **`ctrl+p` opens Textual's own command palette**, not ours, and its "show
   keys and help panel" item opens an unstyled side pane with no way out.
   Cause: `App.ENABLE_COMMAND_PALETTE` / `COMMAND_PALETTE_BINDING = "ctrl+p"` is
   a system binding that runs before `App.on_key`. Fix: set
   `ENABLE_COMMAND_PALETTE = False` on `PtuiApp` so keys.toml owns every key.

3. **`z z` (vertical ↔ horizontal split) does nothing visible.** Cause:
   `pane.toggle_layout` flips `styles.layout` on `#panes`, but the list pane
   keeps its explicit `width: 45%`, so the panes stay put. Fix: swap width/height
   constraints along with the layout.

4. **Hiding the info pane leaves the list at its old width.** The list must
   expand to the full window when it is the only pane.

5. **The list overflows horizontally.** Columns must fit the pane: the flex
   column absorbs the remainder and cells truncate (with `wcwidth`, per SPEC
   § "Terminal reality"). No horizontal scrolling.

6. **`/` narrows far too little.** `Nauen` returns obviously unrelated entries.
   Cause: fuzzy narrowing is a subsequence test over *all* narrow fields joined
   into one string, so scattered letters match. Fix: make `substring` the
   default `query.narrow_mode`, and make fuzzy match per field with contiguity
   preferred (rank, don't just filter).

## B. Bound but not implemented (they log "not implemented yet")

- `?` `help.show` — the generated help overlay. SPEC § "Discoverability" layer 3.
- `:` / `ctrl+p` `cmdline.open` — the command line that teaches bindings.
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

## Not yet exercised

Narrow modes other than the default, marks at scale, `lib.switch`, and the
`[modes.files]` keymap.
