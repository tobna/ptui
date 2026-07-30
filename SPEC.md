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
- **Papis version**: pin a tested minimum in `pyproject.toml`. The internal
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

- `edit.mode = "structured"` (default): form editor in the info pane —
  tags, ref, year, authors, custom fields. Validated; cannot emit invalid
  YAML.
- `edit.mode = "editor"`: `App.suspend()` → `$EDITOR` on `info.yaml`,
  full-screen. Do **not** embed a pty in the pane.
- `E` (`doc.edit_raw`) always forces the `$EDITOR` path.

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
- `ui.split_ratio` is the **list pane's** share of whichever axis the split
  currently runs along, and the list keeps the whole window whenever it is the
  only visible pane. Both dimensions are set on every layout change.
- Columns must fit the pane — no horizontal scrolling. A configured width is a
  **ceiling**, not a reservation: a fixed column takes the p90 of what it
  actually holds over the narrowed set, so an 18-cell `Author` shrinks to the 9
  cells real surnames need. One `width = 0` column absorbs the remainder, and a
  fixed column that would starve it is dropped until the terminal is wide
  enough again.
- The sort direction shows in the header of the column the list is sorted by
  (`Year ↓`). A sort key that no column displays changes no header — the status
  bar names the key, and that is the only indicator in that case.
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
   stop typing it.

## Command registry (source of truth)

Keymaps, the `:` command line, the palette, and generated help all derive
from this. Adding a command must not require touching four places.

| command                         | args                          | description                                      |
| ------------------------------- | ----------------------------- | ------------------------------------------------ |
| `nav.down` / `nav.up`           | `count:int=1`                 | move cursor                                      |
| `nav.top` / `nav.bottom`        |                               | first / last                                     |
| `nav.page_down` / `nav.page_up` |                               |                                                  |
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
| `mark.all_filtered`             |                               | all currently narrowed docs                      |
| `mark.invert`                   |                               | within current filter                            |
| `mark.clear`                    |                               |                                                  |
| `mark.show_only`                |                               | toggle marked-only view                          |
| `visual.start` / `visual.line`  |                               | `v` / `V`                                        |
| `doc.open`                      | `which:int?`                  | open via papis opentool                          |
| `doc.open_folder`               |                               | file browser at doc folder                       |
| `doc.browse`                    |                               | `papis browse` — URL/DOI                         |
| `doc.notes`                     |                               | open/create notes                                |
| `doc.edit`                      |                               | per `edit.mode`                                  |
| `doc.edit_raw`                  |                               | force `$EDITOR`                                  |
| `doc.delete`                    |                               | delete dialog                                    |
| `doc.add`                       | `source:str?`                 | add flow; `source="inbox"` picks from inbox dir  |
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
| `view.doctor`                   |                               | doctor findings view                             |
| `view.saved`                    |                               | saved searches view                              |
| `doctor.run`                    | `checks:str?`                 | filterable results; **reports only, never fixes** |
| `doctor.fix`                    |                               | apply fix for one selected finding, via safe-write |
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
| `app.quit`                      |                               |                                                  |
| `picker.confirm`                | `invert:bool=false`           | modal only                                       |
| `picker.cancel`                 |                               | modal only                                       |

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
