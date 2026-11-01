<h1 align="center">ptui</h1>

<p align="center">
  A fast, keyboard-driven terminal UI for
  <a href="https://github.com/papis/papis">papis</a>.<br>
  <em>v0 — browsing, searching, marking, opening and filing. Early days.</em>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.13%2B-blue">
  <img alt="Built with Textual" src="https://img.shields.io/badge/built%20with-Textual-5a5aff">
  <img alt="papis" src="https://img.shields.io/badge/papis-0.16.x-b8283c">
</p>

---

## Why

`papis` is a great library manager and a slow browser. ptui is the browsing
half: open the library, find the paper in three keystrokes, hit `o`.

- **Two-layer search** — a real papis query for the scope, an instant in-memory
  filter on top. The status bar always shows both, so a missing document is
  never a mystery. `/` takes terms: `vision -survey a:nauen y:>2023`, all ANDed,
  matches highlighted as you type, 2 ms across a 750-document library.
- **Vim keys, discoverable** — which-key popups after a prefix, a context hint
  bar, and a `?` overlay listing the *effective* keymap of whichever mode you are
  in, generated from `keys.toml` so it can never drift.
- **Picks up where you left off** — the scope, sort and cursor of your last
  session in each library (`general.persist_state`).
- **Update without losing your edits** — `c u` refetches a document from its
  DOI, arXiv id or title and merges: missing fields are filled, and every real
  disagreement is a question. `papis update --auto` just overwrites.
- **Read before you open** — the info pane shows the abstract under the
  metadata; `J`/`K` scroll it without leaving the list.
- **Marks that survive** — marks are keyed by document, not by row, so sorting
  and filtering never lose them. Batch operations confirm against the real count.
  `v` marks a range as you move (`V` unmarks one), the way vim's visual mode does.
- **Safe by construction** — every write ptui makes re-checks `info.yaml`'s
  mtime, writes atomically, and leaves unknown keys and comments untouched.
  Files move via `os.link`, never a clobbering `mv`.
- **Add from anywhere papis can reach** — `a` lists the sources: a file, the
  inbox, an arXiv id, a DOI, an ISBN, a `.bib`, a publisher URL, or a PDF to read
  an id out of. arXiv adds fetch the paper too. The list comes from papis's own
  importer plugins, so installing one adds a row. `f a` attaches a file or URL
  to the document you are on, exactly as `papis addto` would.
- **Merge duplicates** — mark the copies, `m m`, and keep the citekey you want.
  Fields only one record had are filled in, real disagreements ask, attachments
  are unioned, and the folded-in folders go to the trash rather than the void.
- **Doctor without the surgery** — findings show up in the info pane for the
  document you are on, `! !` narrows the list to every document that has any,
  and nothing is written until you ask (`! f`, or `! o` to pick one finding).
  papis's own `doctor.run` fixes by default; ptui never calls it.
- **Files that file themselves** — ordered rules decide where a PDF belongs;
  `f r` relocates and renames a whole selection to the scheme, skipping anything
  it does not understand.
- **SSH-friendly** — `icons = false` swaps every nerd-font glyph for its ASCII
  twin at the same width, no forked worker pools, and the info pane moves under
  the list by itself once the title column would get squeezed.
- **Tells you what a document is** — one column, one cell: conference paper,
  journal article, thesis, or an arXiv-only `preprint` worked out from the
  metadata. Nerd-font glyphs by default, ASCII on request.
- **Edit fields without leaving the list** — `c t` adds tags (keeping the ones
  already there; comma-separated, so `machine learning` is one tag), `c T`
  removes them, `c s` sets the reading status, `c r` the
  rating, and `c f` any field at all, with the type papis declares for it
  (`tags` a list, `year` an int). All batch-aware over marks, batches confirm
  first, every write through the safe-write path. `e` opens the whole
  `info.yaml` in `$EDITOR`, telling you if you saved YAML papis can no longer
  parse; `E` a form over the common fields (typed, one undo step).
  `edit.mode = "structured"` swaps the two.
- **Looks like your editor** — 22 themes, live-switchable with `\ t`: the
  catppuccin family, gruvbox, nord, rosé pine, solarized, dracula, and
  `tokyonight-moon` (LazyVim's default) shipped as the out-of-the-box palette.
  Titled panes, a lualine-shaped status bar, and marks in the theme's accent.
- **Delete you can take back** — `d d` asks first, against the *total* marked
  count with the refs listed, and every file gets a checkbox that is ticked only
  when the file lives somewhere ptui manages. `u` puts it all back: folders
  return from the trash, or, with `undo.strategy = "git"`, ptui's own commit is
  reverted. Files always route through the trash whatever the strategy, because
  your PDF root is usually outside the repo.
- **Every command by name** — `:` (or `ctrl+p`) fuzzy-completes over the whole
  command registry with each command's key binding beside it, so the keymap
  teaches itself while you use it.

- **Searches you come back to** — `\ s` saves the scope and sort under a name,
  `g s` lists them next to the ones in `config.toml`, and `up` in any query
  prompt recalls what you typed before. `g r` jumps to a pasted
  `\cite{key}`; `m q` marks by query without touching the view.

- **A files pane** — `3` lists the document's files in order; `x` detaches one
  (the file stays), `K`/`J` reorder, `p` repoints a broken path at a file in the
  folder, `r` renames to papis's own `add-file-name` scheme, `f n` tidies path
  styles. Every one of them is a single `u` away from undone.

## Install

Not on PyPI yet — install from a checkout:

```sh
git clone https://github.com/tnauen/ptui && cd ptui
uv sync
uv run ptui                 # or: uv run papis ptui
```

To get `ptui` on your `$PATH`:

```sh
uv tool install --editable .
```

`papis ptui` only works when ptui lives in the *same* environment as papis — a
plugin is found through papis's own entry points. If your papis came from pipx:

```sh
pipx inject --editable --include-apps papis /path/to/ptui
```

## Configure

Config lives in `~/.config/papis/ptui/`:

| file            | what                                                |
| --------------- | --------------------------------------------------- |
| `config.toml`   | panes, columns, sorting, file-placement rules        |
| `keys.toml`     | every binding, plus which-key and hint-bar behaviour |

The shipped defaults in [`src/ptui/defaults/`](src/ptui/defaults) are the
reference; your files hold only what you change. In `keys.toml` a binding of
yours replaces every shipped one it collides with — the same key, a prefix of it
or one it is a prefix of, or another key for the same command — and `"o" = false`
unbinds a key:

```toml
[modes.list]
"o" = { cmd = "doc.open_folder" }   # swap o and O
"O" = { cmd = "doc.open" }
"enter" = { cmd = "doc.open" }      # O took doc.open from enter as well: keep it
"b" = false                         # unbind
```

Values papis already owns (library paths, `editor`,
`opentool`) come from papis config unless you override them.

## Keys

`j`/`k` move · `h`/`l` previous/next pane · `o` open · `/` filter · `s` search ·
`space` mark · `S` sort · `a` add · `i` add from inbox · `e` edit `info.yaml` ·
`\ t` theme · `q` quit.

Prefixes are namespaces, and hold one to see its menu: `g` go · `f` files ·
`y` yank · `c` change · `m` marks · `z` layout · `d` delete · `\` admin.
So `y y` yanks a `\cite{…}`, `f r` files a PDF where it belongs.

Sorted by "recently added" and everything sits in one lump at the bottom?
papis stamps `time-added` only from `papis add` on the command line, so
documents that arrived any other way have none. ptui stamps it on every document
it adds (unless papis's `time-stamp` is off); run `:lib.backfill_dates` once to
fill it in for the rest, from each `info.yaml`'s modification time.

**[KEYS.md](KEYS.md) lists every binding** — key, command name and what it does,
per mode, generated from the shipped keymap. `?` shows the same table inside the
app, for whichever mode you are in.

## Development

```sh
uv sync
uv run pytest
uv run ruff check --fix && uv run ruff format
```

CI ([`.github/workflows/test.yml`](.github/workflows/test.yml)) runs the same
`uv sync` and `uv run pytest` on every push and pull request, on Python 3.13.

Design contract: [`SPEC.md`](SPEC.md). Agent notes: [`CLAUDE.md`](CLAUDE.md).

## License

MIT
