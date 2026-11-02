# TODO — backlog from real use

Feedback from the first sessions with a real library. Ordered roughly by how
much it hurts. Root causes are noted where they are already understood; they
were traced in the code, not guessed. Finished items are deleted, not struck
through — what shipped is described in `CLAUDE.md`, and the leftovers each
entry left behind stay here as their own item.

## A. Broken

Nothing known. (`SPEC.md` is the contract; report against it.)

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

Nothing open: the `:` commands of `SPEC.md` are all built.

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

## H. From the commit-by-commit review of `test-and-improve` (2026-11-01)

Worked in this order, one commit each; an entry is deleted in the commit that
finishes it. The number in brackets is the review question it came from.

11. **Open, needs a decision before building:** an option to write a
    `:config.set` change into `config.toml`. Keeping the user's comments needs
    `tomlkit`, a new dependency.
63. **A Crossref request that fails reads as "found nothing".**
    `papis.crossref.get_data` catches every error, logs "Error getting works
    from Crossref." and returns `[]`; `fetch.crossref` turns `[]` into
    "Crossref found nothing for …". Offline, `c u` and the `crossref` add
    source say the paper does not exist. Tell a failed request from an empty
    answer and say Crossref could not be reached. With a test. [129 a]
64. **`safewrite.read` keeps a record of every file it parsed.** The shared
    ruamel `YAML` instance appends a `DocInfo` to `_yaml.doc_infos` on every
    load (measured: 2000 reads, 2003 entries, ~160 B each) and nothing clears
    it, so it grows for the whole session. Load through a fresh instance, as
    `_dumper()` already does for dumps. [129 c]
65. **"press E again" names the wrong key.** When `$EDITOR` leaves
    `info.yaml` unparsable, `doc.edit_raw` says "press E again", but with the
    shipped `edit.mode = "editor"` the editor is `e` and `E` opens the form.
    Name the key the keymap binds to the editor. [132 a]
66. **`f r` and the doctor's fixes cannot be undone and do not confirm a
    batch.** `files.relocate` and `doctor.fix` push no undo step, so `u`
    undoes the step before them, and on many marked documents they act at
    once, where SPEC says destructive batch operations confirm. One undo step
    per run, and the batch confirm `_set_field` uses. With tests. [132 b]
67. **The config folder is documented wrong.** SPEC and CLAUDE.md say
    `$XDG_CONFIG_HOME/papis/ptui/`, but `config.config_dir()` is papis's
    `get_config_folder()` plus `ptui`, which honours `$PAPIS_CONFIG_DIR` and
    is elsewhere on macOS. Say "`ptui/` in papis's config folder", with the
    usual Linux path as the example. [132 c]
68. **Stale docs.** CLAUDE.md's Status gives a test count: drop it, it is
    stale by the next commit. CLAUDE.md and `fetch.available()`'s docstring
    say a new importer plugin shows up in `a` for free, but `available()`
    offers only the names in `fetch.SOURCES`. SPEC's "Not in v0" still lists
    the structured editor, undo and saved searches. SPEC, CLAUDE.md and
    `actions._reread` still say `r` only re-asks papis's cache, which H.60
    changed. Then trim CLAUDE.md to what only it says: drop the "Built:"
    inventory, repeats, notes on removed things, and anything that restates
    SPEC, README, KEYS, TODO or a docstring, pointing there instead. [132 d,
    133.1]
26. **A README for people**, written carefully and readably, describing the
    project as it stands once the entries above are done.

## Not yet exercised

Regex narrow mode, marks at scale, `lib.switch`, and the `[modes.files]` keymap.
