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
47. **`y y` on a document with no `ref` yanks `\cite{}`.** The empty-key
    filter runs after `citekey_format` has wrapped the ref, so it never sees
    an empty key. Skip the document and say so. (Pinned in
    `tests/test_cover_actions_open.py`.)
48. **`\ c` calls `files.pdf_root` an unknown key**, though the shipped
    `config.toml` tells you to set it (it is only in a comment there). (Pinned
    in `tests/test_cover_actions_cmdline.py`.)
49. **Empty the trash by itself.** At startup, delete what has been in the
    trash longer than `undo.trash_retention_days`, with one log line saying how
    much went. A new `undo.auto_empty_trash`, shipped `true`, turns it off.
    Once ptui is closed `u` cannot restore from the trash anyway, so it is only
    a safety net. SPEC's "never unasked" for `lib.empty_trash` goes with it.
51. **After an outside edit, the list row keeps the old title.** Edit a title
    in `info.yaml` outside ptui, then `:app.reload true`: the info pane shows
    the new title, the row the old one, until the file is written again.
    `cell_text` stamps its cache with the file's *current* mtime while
    formatting the stale in-memory document, so the redraw the `:` prompt
    causes stores the old text under the new stamp. `doctor.CACHE` has the
    same flaw (H.4's background re-check runs on the stale copy and stamps
    the result with the new mtime): fix both, by re-reading the document
    from disk before anything is stored under a newer stamp. (Pinned in
    `tests/test_session_sync.py`.)
52. **Ask about the same title in a different year on add.** H.15's check
    knows the same title in the same year; the same title in another year
    (a preprint and its journal version, a reprint) should ask "is this the
    same paper?" too, with the same go to / add anyway / cancel choice.
53. **Low priority: `/bert` should find `{B}ERT`.** The list shows the title
    without LaTeX braces, but `/` searches the stored text, so what you see
    is not what you can find.
54. **Blank rows under the files pane.** At 80x24 in the stacked layout,
    `z i` and then `3` leave about five empty rows under the files pane.
55. **The file verbs and the edit form overwrite an outside change.** H.45
    fixed the `c` verbs; `x`, `J`/`K`, `p`, `r`, `f n` and the `E` form still
    build what they write from the copy ptui loaded, so a change made to the
    same document on disk since then is lost. Write tests that change the
    file on disk first, then fix each one.
56. **Show several Crossref hits.** papis's Crossref importer gets every hit and
    keeps the first ("Picking the first one!"). For `c u` and for adding from
    Crossref, call papis's `papis.crossref.get_data(query=…, max_results=…)`
    and list the hits (title, year, first author, venue) in the picker, with
    "none of these"; one hit keeps H.24's "is this the paper?".
57. **A refused write re-reads the document.** When an edit is refused because
    `info.yaml` changed on disk ("press r to reload"), `r` does not help: it
    re-asks papis's cache, which never saw the outside edit. Re-read that one
    document from disk right there and say "reloaded from disk; do it
    again", at every call site of the message. [98.2]
58. **The hint bar never cuts an entry.** At 80 columns it ends in `space`
    with no description, and in the files pane at 120 it cuts `x remove from
    the list, keep`. Use the short `desc` from `keys.toml`, and leave out an
    entry that does not fit whole. [98.3]
59. **`g r` prefers the one match shown.** Near matches stay searched in the
    scope only, as now. When several refs in the scope match but exactly one
    is shown in the narrowed list, go to that one; still several, list them
    as now. Nothing outside the scope is searched or reported. [104, 59.1]
44b. **An error after a picker answer ends the session.** H.44 guarded what
    runs after a prompt; what runs after `enter` in a picker (`ui.pick`'s
    `on_confirm`) and after the forms and dialogs (`push_screen` callbacks)
    is still unguarded. Guard them the way `run_command` is. [107.1]
60. **`r` reloads from disk.** Today `r` re-asks papis's cache, which never
    sees a file changed outside papis (vim, a sync, `git pull`); only
    `:app.reload true` does. Make `r` the rescan (`args = { rescan = true }`
    in `keys.toml`, desc "reload library from disk"), and update SPEC's
    command table and KEYS.md. [98.2]
26. **A README for people**, written carefully and readably, describing the
    project as it stands once the entries above are done.
27. **More and better tests**: more cases where they find bugs — the edges each
    entry above turned up, the paths only one test walks today — rather than a
    count.

## Not yet exercised

Regex narrow mode, marks at scale, `lib.switch`, and the `[modes.files]` keymap.
