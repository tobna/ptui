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

4. **The doctor re-checks stale documents in a background thread.** Today a
   stale entry is re-scanned inside the redraw (~2 ms each), so a mass change
   on disk (a sync, a `git pull`) freezes the next redraw. While the thread
   runs, a stale document keeps showing its old findings. [13.1, 13.2]
8. **A doctor scan with named checks keeps the other findings.** `:doctor.run X`
   and `:doctor.fix X` replace a document's whole cache entry with X's
   findings, so every other finding's marker vanishes until `! d`. Replace only
   X's findings. Do it together with 4. [26.1]
11. **Open, needs a decision before building:** an option to write a
    `:config.set` change into `config.toml`. Keeping the user's comments needs
    `tomlkit`, a new dependency.
14. **`g r` without an exact match** logs a warning, and if the key is a
    substring of exactly one `ref`, jumps to that one. [28.1]
15. **Deduplication on add** (DOI, arXiv id, title + year). First find what
    `papis add` already does and reuse it (golden rule 3).
17. **Test `:doc.add <source> <id>` for `doi` as well as `arxiv`**: parametrise
    `test_doc_add_with_a_uri_skips_both_prompts`. [30.2]
18. **Editing the author line in the form keeps each author's extra keys**
    (affiliation, ORCID, …) when the name still matches; today the whole
    `author_list` is rebuilt from the line. [32.2]
20. **`f n` never follows symlinks, and a relative entry is always the shortest
    one.** It resolves the file through symlinks but builds the relative path
    from the folder as papis names it, so a library reached through a symlink
    gets entries like `../../../../data/cloud/papers/doc/paper.pdf` — wrong,
    not merely ugly — and a symlinked PDF is replaced by its target. Build both
    sides from the same unresolved form (`paper.pdf`). A relative entry never
    climbs to `/`: when that is the only relative path, write the absolute path
    and log that it did. Tests with a symlinked library folder and a symlinked
    PDF. [37.2, 38.0]
21. **`v`/`V` + `g r`**: pin with a test that the range stretches to the jump
    target (it works). When `g r` has to drop a narrow to reach the target, the
    range ends instead of stretching — that one is a decision for the user, not
    to be guessed.
24. **`c u` from Crossref asks first.** A title search can match the wrong
    paper and fill gaps from it; show what was found (title, year, first
    author) and ask "is this the paper?" before planning anything. [42.1]
25. **Data loss: an empty export overwrites its target.** papis's export returns
    empty text on failure (`csl` without `citeproc-py`), and `export.as` then
    writes it over the target file, or copies it and reports success. Write
    nothing, copy nothing, log the failure. [43.1]
29. **A dangling symlink at the destination crashes `place()`.**
    `os.path.samefile` on the dead link raises `FileNotFoundError` instead of
    `place()` returning a `conflict` or `error` status. Pinned by the xfail
    `test_a_dangling_symlink_at_the_destination_is_a_conflict_not_a_crash` in
    `tests/test_edge_place.py`. Worked after 25, before the README.
35. **`doctor.fix` misses fixes made in place.** It diffs against a shallow
    `dict(doc)`, so papis's `files` fix writes nothing, and string-cleaner's
    initials fix writes `author` but not `author_list`. [test_edge_doctor.py]
39. **A `KeyError` inside a command reads as "not implemented yet"**, and its
    traceback is dropped: `run_command` takes every `KeyError` for an unknown
    name. [test_edge_app.py]
44. **An error after a prompt ends the session.** What runs after a prompt is
    submitted (`s`, `/`, `\ s`, `g r`, `m q`, the `:` line's arguments, …) has
    no guard, unlike a command started from a key, so any exception there
    closes ptui. Example: `query.saved_file = ""`, then `\ s` and a name.
    Guard it the way `run_command` is guarded. (Pinned in
    `tests/test_cover_actions_queries.py`.)
46. **Low priority: a `files:` string is split into letters.** A hand-written
    `files: paper.pdf` (one string, not a list) is iterated letter by letter,
    so `x` and `J` write `[p, a, p, e, r, …]` over it. Read a string as a
    one-entry list. (Pinned in `tests/test_cover_actions_files.py`.)
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
26. **A README for people**, written carefully and readably, describing the
    project as it stands once the entries above are done.
27. **More and better tests**: more cases where they find bugs — the edges each
    entry above turned up, the paths only one test walks today — rather than a
    count.

## Not yet exercised

Regex narrow mode, marks at scale, `lib.switch`, and the `[modes.files]` keymap.
