# ptui — a Textual TUI for papis

## Scope of this document

Implementation spec. `config.toml`, `keys.toml` and `themes/*.tcss` are the
user-facing surface; this document defines invariants, the command registry,
and the data model those files refer to.

Package name `ptui` is a placeholder — `papis-tui` is taken by an existing
curses project (supersambo/papis-tui), which is worth reading for prior art
on keychain hints and search aliases.

## Architecture decisions

- **Stack**: Python + Textual. Ships as a papis plugin via the
  `papis.command` entry point (`ptui = "ptui.cli:main"`). A `papis.picker`
  entry point (so ptui can serve as `picktool`) is post-v0.
- **All reads/writes go through the papis API** (`papis.database`,
  `papis.document`, `papis.config`). Never parse or write `info.yaml`
  directly except through the safe-write helper below. Papis caches; direct
  writes desync it.
- **Documents are identified by `papis_id`**, never folder path. Paths change
  under `papis rename`.
- **Config lives in `$XDG_CONFIG_HOME/papis/ptui/`** (`config.toml`,
  `keys.toml`, `themes/*.tcss`). TOML rather than papis's own INI file
  because the attach rules are an ordered list of tables, which configparser
  cannot express. Values papis already owns — library paths, `editor`,
  `opentool`, `use-git`, `add-file-name` — are read from papis config and not
  duplicated; `config.toml` may override per key.
- **Papis version**: pinned `>=0.15.0,<0.16` in `pyproject.toml`. The ceiling is
  deliberate — papis already warns about API it removes in 0.16. Raise it only
  after running the suite against a real 0.16. The internal
  API changed between 0.14 and 0.15 and an upstream client/server refactor is
  in progress. Feature-detect (`hasattr`) rather than version-sniff where
  practical; fail loudly with a clear message otherwise.
  **ACTION FOR IMPLEMENTER: verify every papis API call against the installed
  version before use. Do not trust this document's API names.**

## Data model

### What ptui writes to `info.yaml`

Only these, and only on explicit user action:

- standard papis keys (structured editor or `$EDITOR`)
- `files` — list of path strings (see File semantics)
- `tags`
- custom keys, all optional:
  - `reading_status`: `unread | reading | read` (free string tolerated)
  - `rating`: int 0–5
  - `opened_at`: ISO8601, written on `doc.open` if `general.track_opens`

### Round-trip invariant (hard requirement)

Unknown keys, key order, and comments-where-possible MUST survive a ptui
write untouched. The user's external scripts add keys ptui has never heard
of. Test this explicitly.

### File semantics

`files` is a flat list of path strings. Entries may be relative to the
document folder, relative with traversal (`../../pdfs/x.pdf`), or absolute.
ptui preserves each entry's existing style unless the user runs an explicit
command. New entries use the `path_style` of the matching rule.

There is **no `kind` field**. Main vs. notes vs. supplement is _inferred_
from `files.kind_patterns`, never written to disk — a parallel key would
desync with an external script that appends to `files`.

### Safe write (required for every mutation)

1. Record `st_mtime_ns` of `info.yaml` at read time.
2. Before writing, re-stat. If changed → abort, reload, tell the user.
   Never merge silently.
3. Write `info.yaml.ptui.tmp` in the same directory, `fsync`,
   `os.replace()` onto the target.
4. If git is active, stage and commit (see Undo).

Papis has no locking. Two ptui instances, or ptui plus a user script, will
interleave eventually. This is the whole defence.

## File placement — `place()`

One function, three callers: the add flow, `files.relocate`, and the doctor
check `file-not-canonical`. No duplicate logic anywhere.

```
place(doc, src_path, rules, *, force=False, dry_run=False) -> PlaceResult
```

| status      | meaning                                 | default action             |
| ----------- | --------------------------------------- | -------------------------- |
| `ok`        | placed                                  | update `files`             |
| `already`   | dest exists, same file (realpath/inode) | no-op                      |
| `unmanaged` | matched an `op = "in-place"` rule       | skip                       |
| `conflict`  | dest exists, different file             | skip + report              |
| `duplicate` | dest exists, byte-identical (hash)      | offer repoint + delete src |
| `error`     | IO failure                              | report                     |

### Atomicity (required)

Never `shutil.move` onto a destination. Reserve the name atomically:

```python
# same filesystem
os.link(src, dst)      # raises FileExistsError — cannot clobber
os.unlink(src)

# cross-device
tmp = dst.parent / f".{dst.name}.ptui.{os.getpid()}"
shutil.copy2(src, tmp); fsync(tmp)
os.link(tmp, dst)      # same guard
os.unlink(tmp); os.unlink(src)
```

A check-then-move is TOCTOU and is not acceptable.

### Commit order (required)

File operation first, `info.yaml` second. Roll the file back if the yaml
write fails. The reverse order leaves a dangling reference — worse than an
orphan, because nothing detects it until the user presses `o`.

### Other required checks

- Case-insensitive filesystems: `Foo.pdf` and `foo.pdf` collide on macOS even
  though an existence check passes.
- Collision suffixing must be deterministic: `_b`, `_c`, … never a timestamp
  or random string.
- `place()` must be idempotent — a second run yields `already`. This is what
  makes whole-library migration safe.

## Query model — two layers, do not conflate

1. **Scope** — a real papis query, run against the database on submit.
   Backend-dependent: the sqlite backend only exposes fields listed in
   `sqlite-schema-fields`; whoosh has its own syntax; the default cache
   backend differs again.
2. **Narrow** — in-memory filter over the scoped set. Backend-independent,
   identical on every machine, runs per keystroke in a worker with debounce.

The status bar must always show both — `scope: tags:cv | narrow: transf` —
or users cannot tell why a document is missing.

Narrow is **term-based**: the query splits on whitespace and every term must
match, so typing more characters always shrinks the result. A term is a
case-insensitive substring by default, and may be `-negated`, `"quoted"` to keep
its spaces, qualified as `field:value` (dotted paths included), or a numeric
range — `year:>2023`, `year:2020..2024`. `[query.aliases]` applies to *both*
prompts, so `a:nauen` means the same thing in either. Fuzzy is opt-in via
`query.narrow_mode` and must keep the matched characters close together; a plain
subsequence test over the joined fields matches almost everything and is
forbidden. Narrowing **filters and never reorders** — sorting stays the user's.

The same matcher serves every picker's filter box. One implementation, or they
drift and only one of them gets fixed.

## Adding — sources come from papis

`doc.add` with no argument lists every source: a file, the inbox, a publisher
URL, a `.bib`, and one row per **papis importer** that ptui has a label for.
The list is built from `papis.importer.get_available_importers()`, so a new papis
plugin appears without a change here.

Rules learned by measuring the papis API, all load-bearing:

- **Never call `get_matching_importers_by_uri` on arbitrary input.** Matching
  itself hits the network — the `doi` importer's `match()` HTTP-GETs doi.org for
  *any* string, a local path included, and raises rather than declining. The
  importer is chosen by name from what the user picked.
- URL dispatch is `http(s)` **only**, because the `fallback` downloader matches
  anything and would try to GET a filesystem path.
- Constructors differ per importer (`ArxivImporter` takes `arxivid`, not `uri`),
  so `cls.match(uri)` is the only generic way to build one.
- Importers fetch **files** as well as metadata: `ctx.files` is a PDF papis has
  already downloaded. `place()` then files it by the normal rules.
- A fetched record carries keys the form does not show (abstract, eprint, venue,
  pages). They ride along into the document; dropping them would make importing
  worse than typing it in.
- A document with no file is legitimate — `papis.commands.add.run([])` accepts it.
  A `.bib` import attaches nothing; an arXiv import attaches the PDF it fetched.

## Merging duplicates

`doc.merge` folds the marked documents into one. The same paper arrives twice —
once from arXiv, once from the proceedings — and each record holds fields the
other lacks, so a merge is mostly union with a question only where two records
genuinely disagree.

- **The `ref` you keep is the document you keep.** Choosing which citekey
  survives is the same decision as choosing which folder and `papis_id` survive,
  so it is one question, asked first. When every ref already agrees there is
  nothing to ask.
- Keys only the others had are **filled silently**; there is nothing to choose.
  A key two records disagree on gets one picker, which also offers *keep
  everything else from this document* to settle the rest in one answer.
- **`files` is unioned, never chosen** — losing an attachment is the one outcome
  a merge cannot undo from metadata. Entries are resolved against the folder they
  still live in, *before* it is removed, then routed through `place()` like any
  other file.
- `time-added` becomes the earliest of the group: the document has existed since
  whichever copy came first.
- The folded-in folders are **moved to `undo.trash_dir`**, not deleted, and
  `papis.database.delete` is called for each — moving a folder is not enough,
  papis keeps its own index and a document whose folder has gone still comes back
  from the cache. `papis rm` pairs the two and so does ptui.
- Finding duplicates is a **separate job** from merging them; ptui merges what
  you marked and does not go looking.

## Doctor — a report, not a chooser

Findings are read-only until asked otherwise: nothing papis calls a "fix" runs
without an explicit verb. `papis.commands.doctor.run()` is **never** called — it
takes `fix=True` by default and mutates a document as a side effect of looking
at it. Findings come from each check's own `operate`.

- **Per document: the info pane.** The document under the cursor shows its own
  findings below its files, as an exception marker — a clean document says
  nothing rather than spending a line to say so. This is the whole per-document
  view; there is no modal to open and dismiss.
- **Whole library: the list, narrowed.** `doctor.run` scans the target set,
  writes the flat report to the log pane, and narrows the list to the documents
  that have findings. Jumping to an entry is then just moving the cursor, and
  every other verb still works on the row. `escape` drops the narrow like any
  other. Marks first, otherwise the whole narrowed set — a library-wide check
  cannot say anything about a single document under the cursor.
- **Findings are cached by `papis_id`, stamped with `info.yaml`'s mtime.** A
  background pass at startup fills it (`[doctor] scan_on_startup`); a written
  document reads as *not checked* rather than showing findings from before the
  write. `doctor.run current=true` re-checks one document.
- **Per-document and library-wide checks are different sets.**
  `duplicated-keys` accumulates the values it has seen in papis's module state,
  so running it per document invents a finding on the second look at the same
  record. It runs once, over the whole set, after that state is reset. Despite
  the name, `duplicated-values` is per-document — it looks *inside* one list.
- Fixing is a verb, never the side effect of confirming a chooser: `doctor.fix`
  for every fixable finding on the targets, `doctor.fix_pick` to choose one.

## Sorting

- `sort.picker` opens the shared `SelectList` modal (see below).
- Presets from `list.sort_presets` first, then keys discovered from the
  library if `sort_discover_keys`; fuzzy-filterable across both.
- Rows show label, key, and default direction: `Year ↓ (year)`.
- `enter` applies the key's **own** default direction; `shift+enter` inverts.
  A single global `sort_reverse` is wrong half the time — author ascending,
  date descending.
- Discovered keys are the union of keys across documents, computed in a
  worker and cached against the papis cache generation. Do not rescan on
  every open.
- Sort keys support dotted paths (`author_list.0.family`) via a small
  resolver. Needed because `author` is a formatted string and sorting by
  first author's surname is what people actually want.
- `list.sort_tiebreak` is the secondary key; ties on `year` are otherwise
  arbitrary and the list order will flicker between runs.

**Invariant**: sorting applies to the scoped set and is independent of
narrowing. Changing sort must not clear the narrow filter, and the cursor
must stay on the same document — preserve cursor by `papis_id` across
re-sort, exactly as marks are.

## SelectList — shared modal picker

Build once, use for sort selection, `query.load`, `lib.switch`,
`theme.picker`, `files.open_pick`, `files.repoint`, and doctor check
selection.

```
SelectList(items, *, title, current=None, on_confirm, columns=None)
```

Fuzzy filter as you type, current item pre-highlighted and marked, `enter`
confirms, `shift+enter` confirms with the picker-specific variant, `esc`
cancels. Bindings live in `[modes.picker]`.

Navigation in the picker is `up`/`down` and `ctrl+n`/`ctrl+p`, **not** `j`/`k`:
the filter box has focus so that typing filters, and a key cannot be both a
letter and a motion. `columns` is not implemented — `Item.hint` (a dim
right-hand field: the sort key, a path, the bound keys) covers every current
caller.

## Marks

- A set of `papis_id`. Survives re-sort and re-filter by design.
- **Therefore**: destructive batch operations confirm against the _total_
  marked count with a preview list, not the visible count. Marking 200,
  narrowing to 3, pressing delete must not destroy 200 silently.
- Status bar: `12 marked (4 visible) / 187 shown / 3211 total`.
- `escape` never clears marks (see `escape_chain`); only `m c` does.
- **Every way of marking has a way of unmarking the same set.** Marking 40
  documents by query and then having to clear all marks to undo it is the
  same trap as the count above. The unmark half is an `unmark:bool` argument
  on the marking command, never a second command with its own name: the
  keymap binds the lowercase key to marking and SHIFT to unmarking, per the
  keybinding design contract. `mark.clear` (unmark all) and `mark.invert`
  (self-inverse) already close the set.

## Undo

Strategies `trash | git | none`. **It is a hybrid, not a toggle**:

- Metadata (`info.yaml`) → per `undo.strategy`.
- **Files always route through trash**, whatever the strategy. `pdf_root`
  typically lives outside the library, so git covers none of it.
- `strategy = "git"`: check at startup whether the document's files are
  actually tracked (LFS counts; gitignored PDFs do not) and warn once per
  session if not. Do not advertise an undo that isn't one.
- `strategy = "git"`: **one ptui operation = one commit**, not one per
  document. Message: `ptui: add tag "foraug" to 30 documents`. `app.undo`
  reverts the last ptui-authored commit; the commit log is the undo history.
- Metadata edits additionally get a session-local in-memory undo stack
  (`undo.stack_size`) regardless of strategy.

## Editing

- `edit.mode = "editor"` (default): `App.suspend()` → `$EDITOR` on
  `info.yaml`, full-screen. Do **not** embed a pty in the pane. On return the
  file is re-parsed: invalid YAML is reported and **nothing is reloaded**,
  because papis cannot load the document either and a silent reload would show
  it as having lost every field.
- `edit.mode = "structured"`: form editor in the info pane — tags, ref, year,
  authors, custom fields. Validated; cannot emit invalid YAML. Not built; the
  mode falls back to `$EDITOR` and says so.
- `E` (`doc.edit_raw`) always forces the `$EDITOR` path.
- Field verbs write through the safe write, never the editor: `doc.tag` adds to
  what a document already has rather than replacing it, `doc.untag` removes the
  key along with its last tag, and both are batch-aware. `doc.rating` is an int
  0–5 where 0 clears, since papis declares no type for it.

## Long-running operations

Doctor runs, metadata fetches, batch relocate, narrow filtering, and sort-key
discovery run in Textual workers with progress and cancel. Partial failure is
the normal outcome of a batch: never abort on first conflict — execute the
clean ones, report the rest.

**Log pane is required, not optional.** "8 of 47 failed" must not live in a
toast that vanishes.

## Terminal reality

- Truecolor detection with 256-colour fallback.
- Nerd-font glyphs **on by default** (`ui.icons = true`), because the terminal
  that has them is the common case and the one that does not is a deliberate
  `icons = false`. Every symbol ptui prints comes from one table with an ASCII
  and a nerd-font column (`ui.GLYPHS`); no glyph is ever written inline, or the
  setting silently stops meaning anything. Both columns are one cell wide, so
  flipping the setting never changes a layout — which is what makes the default
  safe to get wrong: over SSH to a bare tty you get tofu, not a broken list.
  A slot with no sensible ASCII equivalent — an info-pane field label, whose
  name is written out beside it — uses a space in the ASCII column.
- **`venue` is a name, never a place.** The conference or journal name is read
  from `booktitle`, `journal`, `journaltitle`, in that order. The `venue` key is
  *not* consulted for it: real libraries store the host city there
  (`Sydney, Australia`), and treating that as a publication name puts a city
  where the journal belongs and tells `kind()` a preprint was published.
- The list carries a one-cell **document kind** column, from `library.kind()`:
  the `type` field, except that an article whose only DOI is arXiv's and which
  names no journal, booktitle or venue reads as `preprint`. Local data only: an
  entry imported from arXiv and never refreshed is indistinguishable from a real
  preprint, and ptui prefers a wrong glyph to a network call while scrolling.
- `ui.layout` is `auto` | `vertical` (panes side by side) | `horizontal` (info
  stacked under the list), and **`auto` is the default**. `auto` chooses side by
  side only while the flexible column would still reach `list.flex_target`
  cells in the narrower pane it would get — a *column* target, not a terminal
  width, so adding or widening a column moves the threshold by itself. On the
  shipped columns that is about 160 terminal cells.
  A manual `z z` ends automatic choice for the session: once the user has said
  which layout they want, a resize must not argue. There is no third
  single-pane state — the list pane can never be hidden anyway, and `z i` hides
  the info pane on demand.
- The **log pane is transient**: it appears when it is focused and disappears
  when focus leaves it, so `escape` closes it like any other transient surface
  and `app.log` is a toggle only in the sense that pressing it again focuses the
  list. Focus never lands on a pane that is not on screen. The info pane is not
  transient — `z i` is an explicit choice and focusing must not undo it.
- `ui.split_ratio` is the **list pane's** share of whichever axis the split
  currently runs along, and the list keeps the whole window whenever it is the
  only visible pane. Both dimensions are set on every layout change.
- Columns must fit the pane — no horizontal scrolling. A configured width is a
  **ceiling**, not a reservation: a fixed column takes the p90 of what it
  actually holds over the narrowed set, so an 18-cell `Author` shrinks to the 9
  cells real surnames need. One `width = 0` column absorbs the remainder, and a
  fixed column that would starve it is dropped until the terminal is wide
  enough again. A column marked `optional = true` is allocated only after every
  required column, and only while the flex column keeps `list.flex_target` cells
  — `Tags` surviving on a 14-cell `Title` is the wrong trade, because the
  required columns are the ones that identify a document.
- The sort direction shows in the header of the column the list is sorted by
  (`Year ↓`). A sort key that no column displays changes no header — the status
  bar names the key, and that is the only indicator in that case.
- `list.row_height` is the number of lines a document gets, default 1. Above 1
  only the flexible column wraps — it is the one holding a title long enough to
  need the room — and ptui wraps it itself rather than letting the table guess,
  so the ellipsis lands where the text actually stops. Every wrapped row is cut
  to the column width, including a single word wider than the column.
- Truncate by terminal cells, not characters: CJK and combining marks. Cut at a
  word boundary — the last colon in the budget, else the last space — never
  mid-word, unless the boundary would waste most of the width.
- List values (`tags`) are joined for display, never shown as a Python repr.
  This applies wherever a stored value is rendered: list columns and info pane.
- Titles may contain LaTeX (`{B}ERT`, `$\ell_2$`). Store verbatim; render
  de-braced per `list.strip_latex`. Citekey yank and bib export always use
  the verbatim stored value.

---

# Keybinding design contract

1. **Single letters are verbs on the current document** — the ~12 things done
   constantly.
2. **Prefixes are namespaces, and a namespace letter is never also a verb.**
3. **Shift = variant of the same idea** (`o`/`O`, `e`/`E`, `f r`/`f R`).
4. **Rare and administrative commands live under leader `\`.**

Namespaces: `g` go · `f` files · `y` yank · `c` change · `m` marks ·
`z` layout · `d` delete · `\` admin.

`n` / `N` are deliberately left unbound, reserved for next/prev match if a
jump-style narrow mode is added.

**Second hard invariant**: no mode may trap the keyboard. Outside `[modes.list]`,
`escape` is handled by the dispatcher *above* the keymap — it discards any pending
chord and returns to the list — so a mode is free to define very few bindings, or
none at all. In the list mode `escape` keeps its `escape_chain` meaning.

**Hard invariant**: no single-key binding may be a proper prefix of any chord
in the same mode. `keymap.check` runs automatically at config load and
**refuses to start** on a conflict, naming both bindings. Verify at load
time, not keypress time — a shadowed key is invisible until someone wonders
why `o` feels slow.

## Discoverability — four layers, all derived from the command registry

1. **Which-key**: after `which_key_delay_ms` on a prefix, show that
   namespace's bindings with descriptions, sorted, in a corner panel.
2. **Hint bar**: 5–6 context-relevant bindings on the bottom row, changing
   with focused pane and whether marks exist (marks present → surface `d d`,
   `c t`, `y b`).
3. **`?` help overlay**: grouped by namespace, fuzzy-searchable, showing
   _effective_ bindings after user overrides. Never a static text blob. Like
   `escape`, it is guaranteed in every mode by the dispatcher — it follows
   whatever `[modes.list]` binds `help.show` to — and it lists the current
   mode's own table, marking commands that are not implemented yet. Browsing
   is read-only; running a command by name is layer 4's job.
4. **`:` command line**: fuzzy completion over command names **with the bound
   key shown beside each entry**. This is the layer that actually teaches the
   keymap — users find `files.relocate` by typing, see `f r` next to it, and
   stop typing it. It lists the registry, not the keymap, so an unbound command
   is still reachable, and unlike layer 3 it runs what is picked. Implemented as
   the shared `SelectList`, followed by an argument prompt for any command that
   takes parameters — `sort.by` asks for `key [reverse]`, positional and
   shell-quoted, and an empty line keeps the defaults.

## Command registry (source of truth)

Keymaps, the `:` command line, the palette, and generated help all derive
from this. Adding a command must not require touching four places.

| command                         | args                          | description                                      |
| ------------------------------- | ----------------------------- | ------------------------------------------------ |
| `nav.down` / `nav.up`           | `count:int=1`                 | move cursor                                      |
| `nav.top` / `nav.bottom`        |                               | first / last                                     |
| `nav.page_down` / `nav.page_up` |                               |                                                  |
| `nav.goto`                      | `ref:str`                     | move the cursor to a document by `ref`, exact — for a citekey pasted from a `.tex` |
| `pane.focus`                    | `pane:list\|info\|files\|log` | direct focus                                     |
| `pane.cycle`                    | `back:bool=false`             | tab / shift-tab                                  |
| `pane.toggle`                   | `pane:str`                    | show/hide info or files pane; the list never hides |
| `pane.toggle_layout`            |                               | horizontal ↔ vertical split                      |
| `pane.resize`                   | `delta:float`                 | adjust split ratio                               |
| `query.scope`                   | `q:str?`                      | papis query (DB-backed)                          |
| `query.narrow`                  | `q:str?`                      | in-memory filter                                 |
| `query.clear`                   |                               | clear narrow, keep scope                         |
| `query.save`                    | `name:str`                    | save scope + sort                                |
| `query.load`                    | `name:str?`                   | SelectList over saved searches                   |
| `sort.picker`                   |                               | modal sort selector                              |
| `sort.by`                       | `key:str, reverse:bool?`      | direct; omitted `reverse` → key default          |
| `sort.reverse`                  |                               | toggle direction of current key                  |
| `sort.cycle`                    |                               | cycle presets — _registered, unbound by default_ |
| `mark.toggle`                   |                               | advances per `marks.advance`                     |
| `mark.all_filtered`             | `unmark:bool=false`           | all currently narrowed docs                      |
| `mark.query`                    | `q:str, unmark:bool=false`    | every document matching a narrow query, without changing the view |
| `mark.invert`                   |                               | within current filter                            |
| `mark.clear`                    |                               | unmark everything — the twin of `mark.all_filtered` |
| `mark.show_only`                |                               | toggle marked-only view                          |
| `visual.start` / `visual.line`  |                               | `v` / `V`                                        |
| `doc.open`                      | `which:int?`                  | open via papis opentool                          |
| `doc.open_folder`               |                               | file browser at doc folder                       |
| `doc.browse`                    |                               | `papis browse` — URL/DOI                         |
| `doc.notes`                     |                               | open/create notes                                |
| `doc.edit`                      |                               | per `edit.mode`                                  |
| `doc.edit_raw`                  |                               | force `$EDITOR`                                  |
| `doc.delete`                    |                               | delete dialog                                    |
| `doc.add`                       | `source:str?, uri:str?`       | add flow; `source="inbox"` picks from inbox dir; `uri` skips both modals (`:doc.add arxiv 2607.00687`) |
| `doc.set`                       | `key:str, value:str`          | set a field, batch-aware                         |
| `doc.tag` / `doc.untag`         | `tags:str`                    | batch-aware                                      |
| `doc.status`                    | `value:str?`                  | `reading_status`                                 |
| `doc.rating`                    | `value:int?`                  | 0–5                                              |
| `files.relocate`                | `force:bool=false`            | `place()` over all files                         |
| `files.attach`                  | `path:str?`                   | from path, clipboard, or inbox                   |
| `files.detach`                  | `index:int`                   | drop from list, keep file                        |
| `files.repoint`                 | `index:int`                   | fix broken path via SelectList                   |
| `files.rename`                  | `index:int`                   | rename to scheme in place                        |
| `files.reorder`                 | `index:int, to:int`           | first entry is "the" file                        |
| `files.open_pick`               |                               | SelectList over this doc's files                 |
| `files.normalize`               |                               | rewrite path styles — explicit only              |
| `export.bibtex`                 | `target:str?`                 | marked or current → file/clipboard               |
| `export.citekey`                |                               | per `export.citekey_format`                      |
| `export.path`                   |                               | absolute path of main file                       |
| `export.url`                    |                               | DOI/URL                                          |
| `view.saved`                    |                               | saved searches view                              |
| `doctor.run`                    | `checks:str?`, `current:bool` | scan → log + narrow to what has findings; **never fixes** |
| `doctor.fix`                    | `checks:str?, current:bool`   | every fixable finding on the targets, via safe-write; `checks` fixes one check across the library |
| `doctor.fix_pick`               |                               | SelectList over this document's findings; `enter` fixes that one |
| `lib.switch`                    | `name:str?`                   | SelectList over libraries                        |
| `theme.picker`                  |                               | SelectList over `themes/*.tcss`                  |
| `help.show`                     | `filter:str?`                 | generated help overlay                           |
| `cmdline.open`                  |                               | `:` command line with completion                 |
| `keymap.check`                  |                               | report conflicts + shadowed prefixes             |
| `app.escape`                    |                               | resolve per `escape_chain`                       |
| `app.reload`                    |                               | clear papis cache + reload                       |
| `app.undo` / `app.redo`         |                               | per undo strategy                                |
| `app.log`                       |                               | log pane                                         |
| `app.config_check`              |                               | report unknown/invalid config keys               |
| `config.set`                    | `key:str, value:str`          | override one config key for this session only; nothing is written to `config.toml` |
| `app.quit`                      |                               |                                                  |
| `picker.confirm`                | `invert:bool=false`           | modal only                                       |
| `picker.cancel`                 |                               | modal only                                       |

### Commands that exist for the `:` line

Every command above is reachable by name, but these are specified *because* a
key cannot carry their argument — the argument is the whole command. They need
no default binding, and a picker over a closed set is the wrong shape for all of
them: the value is arbitrary text.

| typed                                | why no key replaces it                                                    |
| ------------------------------------ | ------------------------------------------------------------------------- |
| `:doc.set reading_status read`       | arbitrary key *and* value, batch-aware over marks — pure text             |
| `:doctor.fix key-type-check`         | `! f` is all-or-nothing; one check across the library is the real workflow |
| `:config.set list.row_height 2`      | try a setting without editing `config.toml` and restarting; session only  |
| `:mark.query year:>2023 -survey`     | marks by query without disturbing the current narrow, which `m a` destroys |
| `:doc.tag to-read cvpr`              | a picker over the existing vocabulary cannot add a *new* tag              |
| `:doc.add arxiv 2607.00687`          | skips both the source picker and the URI prompt — the paste-a-number path |
| `:files.attach ~/Downloads/x.pdf`    | a file that is not in the inbox has no picker to be chosen from           |
| `:query.save unread-2026`            | a name is text by definition (loading stays a picker)                     |
| `:nav.goto Nauen2026_LUMA`           | exact jump to a citekey pasted from a `.tex`; `/` changes the view        |
| `:export.bibtex ~/paper/refs.bib`    | `y b` yanks to the clipboard; only the argument writes a file             |

Deliberately **not** argument-driven: `query.scope` / `query.narrow` (`s` and
`/` own them), the `pane.*` family (a key is faster than typing), and
`sort.picker` / `lib.switch` / `theme.picker` (a picker over a closed set beats
recalling a name).

## Delete dialog (required behaviour)

- Every path in `files` gets a checkbox.
- Checked by default **only** for files under a managed root
  (`files.pdf_root` or the document folder). Never default-delete a file in a
  directory ptui did not place it in.
- Before deleting a path, query the library for other documents referencing
  the same realpath. If found, warn and default to unchecked. With a shared
  `pdf_root` and a script appending links, two entries pointing at one file
  is a realistic accident.
- "Apply to all N marked documents" for batch deletes.
- Deletions route through trash.

## v0 scope — build exactly this

Two panes; scope query + narrow filter; vim navigation; `doc.open`,
`doc.edit_raw`, `export.citekey`; marks including `mark.all_filtered`; the
add flow with destination preview; `files.relocate`; safe-write; log pane;
`keymap.check`; which-key + hint bar.

**Not in v0**: themes beyond the one built-in, structured editor, undo,
doctor integration, explore/import mode, picker entry point, saved searches,
PDF preview, reading status, ratings.

Ship it, use it daily for two weeks, then extend.

## Testing

- `pytest-textual-snapshot` for UI regressions — important precisely because
  so much is configurable.
- Property-test `place()`: idempotence, no-clobber under concurrent creation
  of the destination, correct rollback when the yaml write fails.
- Round-trip test: `info.yaml` with unknown keys and comments survives edit.
- Keymap test: the prefix invariant holds for every shipped default and
  every example config.
- Fixture library of ~5k synthetic documents; narrow filtering must stay
  responsive while typing.
