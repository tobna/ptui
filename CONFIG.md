# Configuration

ptui reads `config.toml` from the `ptui` folder in papis's config folder,
usually `~/.config/papis/ptui/`. The file does not have to exist: write only
the settings you want to change. Key bindings go in `keys.toml` next to it, and
[KEYS.md](KEYS.md) lists them and their options.

Each setting you write replaces that one setting, and the rest keep their
defaults. A list of tables (`[[list.sort_presets]]`, `[[list.columns]]`,
`[[files.rules]]`) is the exception: yours replaces the shipped list as a
whole, so copy the shipped entries you want to keep.

papis still decides the libraries, the editor, `opentool` and `use-git`; a
setting here overrides papis's value for ptui only. A value that cannot work,
such as a layout that does not exist, falls back to its default with a line in
the log, and `\ c` lists every setting ptui does not know or cannot use. A
TOML syntax error stops ptui at startup and names the file.
`:config.set list.row_height 2` tries a setting for the session without
writing the file.

This page is generated from
[`src/ptui/defaults/config.toml`](src/ptui/defaults/config.toml) by
`uv run python scripts/configdoc.py`.

## `[general]`

| Setting | Default | What it does |
| --- | --- | --- |
| `library` | `""` | empty = papis default library |
| `startup_query` | `""` | scope query applied at launch |
| `persist_state` | `false` | restore last position, scope, sort — per library |
| `state_file` | `"~/.local/state/ptui/state.json"` | where persist_state keeps it |
| `track_opens` | `false` | write `opened_at` on doc.open |

## `[ui]`

| Setting | Default | What it does |
| --- | --- | --- |
| `layout` | `"auto"` | auto \| vertical (side-by-side) \| horizontal (stacked) |
| `split_ratio` | `0.6` | the list pane's share of the split axis |
| `theme` | `"tokyonight-moon"` | any Textual theme; `\ t` lists them live |
| `icons` | `true` | nerd-font glyphs; set false over SSH to a bare tty |
| `mouse` | `true` | false leaves the mouse to the terminal (select, paste) |

## `[list]`

| Setting | Default | What it does |
| --- | --- | --- |
| `flex_target` | `45` | cells the width = 0 column wants; drives ui.layout = "auto" |
| `row_height` | `1` | lines per document; 2 wraps the flex column, halves the rows |
| `null_ordering` | `"last"` | docs missing the sort key; one of `last`, `first` |
| `strip_latex` | `true` | display only; never affects stored data |
| `sort_discover_keys` | `true` | offer arbitrary yaml keys in the sort picker |
| `sort_tiebreak` | `"time-added"` | secondary key; ties on `year` are otherwise arbitrary |

## `[[list.sort_presets]]`

Order here is the order in the sort picker. `dir` is this key's own default.

```toml
[[list.sort_presets]]
key = "time-added"
label = "Recently added"
dir = "desc"
default = true                # applied at startup unless state is restored

[[list.sort_presets]]
key = "year"
label = "Year"
dir = "desc"

[[list.sort_presets]]
key = "author_list.0.family"
label = "First author"
dir = "asc"

[[list.sort_presets]]
key = "title"
label = "Title"
dir = "asc"

[[list.sort_presets]]
key = "opened_at"
label = "Recently opened"
dir = "desc"                  # requires general.track_opens

[[list.sort_presets]]
key = "reading_status"
label = "Reading status"
dir = "asc"
```

## `[[list.columns]]`

Columns are papis format strings. width = 0 means flex.

```toml
[[list.columns]]
title = ""
format = "{doc[kind]}"        # `type`, but an arXiv-only article reads as preprint
glyph = "type"                # render the value through the `type.*` glyph family
width = 3                     # one cell for the glyph, two for the mark in column 0

[[list.columns]]
title = "Year"
format = "{doc[year]}"
width = 6

[[list.columns]]
title = "Author"
format = "{doc[author_list][0][family]}"
width = 18

[[list.columns]]
title = "Title"
format = "{doc[title]}"
width = 0

[[list.columns]]
title = "Tags"
format = "{doc[tags]}"
width = 20
optional = true               # only once the flex column has its flex_target
```

## `[query]`

| Setting | Default | What it does |
| --- | --- | --- |
| `narrow_mode` | `"substring"` | substring \| fuzzy \| regex; terms are ANDed either way |
| `narrow_debounce_ms` | `60` | wait after a keystroke before / filters |
| `narrow_fields` | `["title", "author", "tags", "ref", "year"]` | where a / term without field: looks |
| `history_file` | `"~/.local/state/ptui/query_history"` | up/down in s, / and m q; empty = none |
| `saved_file` | `"~/.local/state/ptui/saved.json"` | what `\ s` saves; [query.saved] below is yours to edit |

## `[query.aliases]`

Typed on the scope prompt, expanded before hitting papis.

| Setting | Default | What it does |
| --- | --- | --- |
| `a` | `"author:"` |  |
| `t` | `"title:"` |  |
| `y` | `"year:"` |  |
| `k` | `"tags:"` |  |

## `[query.saved]`

Saved searches for `g s`: a scope query, or a table with `scope`, `sort` and `reverse` — the shape `\ s` saves in, for the current view.

| Setting | Default | What it does |
| --- | --- | --- |
| `to-read` | `"tags:to-read"` |  |

## `[marks]`

| Setting | Default | What it does |
| --- | --- | --- |
| `advance` | `true` | move cursor down after marking |

## `[files]`

File placement. Rules are ordered; FIRST MATCH WINS. An unmatched file falls through to [files.default] — which is in-place, so ptui never relocates something it does not understand.

By default nothing moves: `papis add` already copies the file into the document folder, and every rule here is in-place, so the relocate pass that runs after an add is a no-op. Set `pdf_root` and add a rule with `dest = "{pdf_root}/..."`, `op = "move"` only if you actually want the PDFs to live outside the library.

| Setting | Default | What it does |
| --- | --- | --- |
| `pdf_root` | `""` | a central PDF store outside the library, for {pdf_root} in a rule; empty = none |
| `collision` | `"skip"` | skip \| suffix \| ask |
| `hash_check` | `true` | detect byte-identical duplicates |
| `inbox` | `"~/Downloads"` | source dir for files.attach and doc.add --inbox |

## `[files.kind_patterns]`

Used to infer main/notes/supplement. Never written to info.yaml.

| Setting | Default | What it does |
| --- | --- | --- |
| `notes` | `["*_notes.pdf", "*-notes.pdf"]` |  |
| `supplement` | `["*_supp*.pdf", "*_appendix*.pdf"]` |  |
| `slides` | `["*_slides.pdf", "*_talk.pdf"]` |  |

## `[files.default]`

| Setting | Default | What it does |
| --- | --- | --- |
| `op` | `"in-place"` | in-place \| move \| copy \| link \| hardlink |
| `path_style` | `"keep"` | keep \| absolute \| relative-doc |

## `[[files.rules]]`

```toml
[[files.rules]]
name = "notes stay put"
match = ["*_notes.pdf", "*-notes.pdf"]
op = "in-place"
path_style = "keep"           # keep | absolute | relative-doc

[[files.rules]]
name = "everything else"
match = ["*"]
op = "in-place"
path_style = "keep"
```

## `[add]`

| Setting | Default | What it does |
| --- | --- | --- |
| `auto_doctor` | `true` | run papis doctor on the new document |

## `[add.sources]`

What `a` offers after a file, the inbox, a URL and a .bib: one row per papis importer, under the name papis registers it by, with the label the picker shows and the example its prompt shows. A row appears only when papis has that importer, in this order, and your own rows come after. Yours merge per key: a new name adds a source, and a shipped name changes that row, so `arxiv = { label = "arXiv" }` relabels it and keeps its example.

| Setting | Default | What it does |
| --- | --- | --- |
| `arxiv` | `{ label = "an arXiv id or URL", example = "2509.26092, arXiv:2509.26092 or an abs/pdf URL" }` |  |
| `doi` | `{ label = "a DOI", example = "10.1109/CVPR52688.2022.01167" }` |  |
| `isbn` | `{ label = "an ISBN", example = "978-0262035613" }` |  |
| `pmid` | `{ label = "a PubMed id", example = "31452104" }` |  |
| `dblp` | `{ label = "a DBLP key or URL", example = "conf/cvpr/HeZRS16" }` |  |
| `zenodo` | `{ label = "a Zenodo id or URL", example = "record id or URL" }` |  |
| `crossref` | `{ label = "a Crossref search", example = "title, author, anything" }` |  |
| `bibtex` | `{ label = "a .bib file", example = "path to a .bib" }` | `:doc.add bibtex` only; the picker's .bib row lists every entry |
| `yaml` | `{ label = "a papis .yaml file", example = "path to an info.yaml" }` |  |
| `folder` | `{ label = "an existing papis folder", example = "path to a document folder" }` |  |
| `lib` | `{ label = "another papis library", example = "a query in that library" }` |  |
| `pdf2doi` | `{ label = "a PDF, read its DOI", example = "path to a PDF" }` |  |
| `pdf2arxivid` | `{ label = "a PDF, read its arXiv id", example = "path to a PDF" }` |  |

## `[doctor]`

papis doctor. ptui only ever *reports* unless you ask for a fix.

| Setting | Default | What it does |
| --- | --- | --- |
| `checks` | `[]` | empty = every check papis registers |
| `scan_on_startup` | `true` | fill the findings cache in the background (~2ms/doc) |

## `[undo]`

| Setting | Default | What it does |
| --- | --- | --- |
| `strategy` | `"trash"` | trash \| git \| none  (files ALWAYS use trash) |
| `trash_dir` | `"~/.local/share/ptui/trash"` | where deleted and merged-away folders go |
| `trash_retention_days` | `30` | days a folder stays there |
| `auto_empty_trash` | `true` | at startup, delete what has been in the trash longer than that |
| `stack_size` | `50` | in-memory metadata undo, any strategy |
| `git_warn_untracked` | `true` | with strategy git, say when git does not track pdf_root |

## `[edit]`

| Setting | Default | What it does |
| --- | --- | --- |
| `mode` | `"editor"` | what e opens: editor ($EDITOR) \| structured (a form); E opens the other |
| `editor` | `""` | empty = papis `editor` / $EDITOR |
| `use_git` | `false` | papis `use-git` wins if set |
| `structured_fields` | `["title", "author_list", "year", "tags", "ref", "doi", "url"]` | the fields of the edit form |

## `[export]`

| Setting | Default | What it does |
| --- | --- | --- |
| `citekey_format` | `"\\cite{{{doc[ref]}}}"` | what y y copies, a papis format string |
| `clipboard` | `"auto"` | auto \| wl-copy \| xclip \| pbcopy \| osc52 |

## `[log]`

| Setting | Default | What it does |
| --- | --- | --- |
| `level` | `"info"` | one of `trace`, `debug`, `info`, `success`, `warning`, `error`, `critical` |
| `max_entries` | `500` | lines the log pane keeps |
| `file` | `"~/.local/state/ptui/ptui.log"` | everything, tracebacks included; empty = none |
