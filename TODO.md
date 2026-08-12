# TODO — backlog from real use

Feedback from the first sessions with a real library. Ordered roughly by how
much it hurts. Root causes are noted where they are already understood; they
were traced in the code, not guessed. Finished items are deleted, not struck
through — what shipped is described in `CLAUDE.md`, and the leftovers each
entry left behind stay here as their own item.

## A. Broken

Nothing known. (`SPEC.md` is the contract; report against it.)

## B. Bound but not implemented (they log "not implemented yet")

- `d d` `doc.delete` — and therefore `u` `app.undo` cannot be tested at all.
  The two are one piece of work: SPEC has a required delete dialog, files route
  through `undo.trash_dir` (`place.trash`, already used by merge) and the index
  needs `papis.database.delete` or the document comes straight back.
- `g s` `view.saved`, `\ s` `query.save`, `\ t` `theme.picker`.
- `f a` `files.attach`, `f n` `files.normalize`, and the `[modes.files]` verbs
  (`files.rename`, `files.repoint`, `files.detach`, `files.reorder`) — there is
  no files pane yet.

## C. Decisions that change the defaults (and so SPEC)

1. **`ui.icons = "auto"` — low priority.** Detect at startup whether the
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

2. **The structured editor is still unbuilt.** `edit.mode` now ships as
   `"editor"`, so `e` and `E` are the same command and `structured_fields` is
   read by nothing. Either build the form or drop the mode and the config key.

## D. Display polish

- **User-defined themes.** The palette is now a Textual theme and ptui ships one
  of its own (`tokyonight-moon`), but there is no way for a user to add theirs
  short of editing `app.EXTRA_THEMES`. A palette file — 12 colours in TOML,
  registered the same way — would cover it. Wait until someone asks: 22 themes
  is a lot of palette already.

- **Nothing is styled per document *kind* yet.** Every row reads the same
  whether it is a preprint, a book or a thesis, and the one-cell kind glyph is
  the only signal. A muted colour per kind is the obvious next idea and also the
  obvious way to make the list noisy — worth a mock-up against the real library
  before anyone builds it.

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

## E. Measurements still owed

1. **Re-measure doctor against the real library.** The old "0 findings over 754
   documents" number was taken with the broken per-document pass and is
   worthless. Re-run per check now that the sets are split, and run the
   library-wide pass once over the whole library — nothing has ever exercised
   `duplicated-keys` honestly.

2. **Nothing finds duplicates for you.** Merge (`m m`) folds a marked group, but
   the group is yours to find; measured, the real library has 0 duplicates by
   title, DOI, eprint or ref. papis's `duplicated-keys` / `duplicated-values`
   checks and a title-similarity pass are the candidates, and both want
   measuring before being trusted.

3. **Nothing re-checks whether a preprint got published.** `kind()` is local
   data only, so the 190 documents that read `preprint` include every arXiv
   import that was never refreshed. A DOI lookup would settle it, but it is a
   network call per document and wants a cache — measure how many of the 190
   resolve to a published DOI before deciding it is worth the machinery.

## F. New features asked for

1. **"Add to"** — attach a file to an _existing_ document (`files.attach`,
   already in the registry and bound to `f a`), routed through `place()`.

2. **The `:` line's own commands** — specified in `SPEC.md` § "Commands that
   exist for the `:` line", none of them implemented. They need no key: the
   argument is the command. In the order they are worth building:
   `doctor.fix checks`, `config.set key value` (session-only, nothing written
   to `config.toml`), `mark.query q [unmark]`, then `nav.goto ref`,
   `doc.add source uri`, `query.save name`. (`doc.set` is built.)

3. **An unmark for every mark.** `mark.all_filtered` and the new `mark.query`
   take `unmark:bool=false`; the keymap gives the SHIFT variant to unmarking
   (`m a` / `m A`, and `m q` / `m Q` when the query one lands), per the
   keybinding design contract. `mark.clear` and `mark.invert` already close the
   set, so this is two arguments and two bindings, not new commands.

## G. Packaging

- **Not on PyPI**, so `uv tool install ptui` / `pipx install ptui` do not exist.
  The README documents the checkout install instead. Publish once the thing is
  worth other people's time.
- **`papis ptui` only works inside the environment that holds ptui.** The
  `papis.command` entry point is resolved through papis's own metadata, so a
  pipx-installed papis cannot see a uv-installed ptui. Documented in the README
  (`pipx inject --editable --include-apps papis <path>`); nothing to fix in the
  code, but expect this question from every user who has papis from pipx or the
  distro.

## Not yet exercised

Regex narrow mode, marks at scale, `lib.switch`, and the `[modes.files]` keymap.
