<h1 align="center">ptui</h1>

<p align="center">
  A fast, keyboard-driven terminal UI for
  <a href="https://github.com/papis/papis">papis</a>.<br>
  <em>v0 — browsing, searching, marking, opening and filing. Early days.</em>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.13%2B-blue">
  <img alt="Built with Textual" src="https://img.shields.io/badge/built%20with-Textual-5a5aff">
  <img alt="papis" src="https://img.shields.io/badge/papis-0.15.x-b8283c">
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
- **Marks that survive** — marks are keyed by document, not by row, so sorting
  and filtering never lose them. Batch operations confirm against the real count.
- **Safe by construction** — every write re-checks `info.yaml`'s mtime, writes
  atomically, and leaves unknown keys and comments untouched. Files move via
  `os.link`, never a clobbering `mv`.
- **Add from anywhere papis can reach** — `a` lists the sources: a file, the
  inbox, an arXiv id, a DOI, an ISBN, a `.bib`, a publisher URL, or a PDF to read
  an id out of. arXiv adds fetch the paper too. The list comes from papis's own
  importer plugins, so installing one adds a row.
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

- **Edit a field without leaving the list** — `c f` sets any field on the
  current document or on every mark, with the type papis declares for it (`tags`
  a list, `year` an int); an empty value clears the field. Batches confirm first,
  and every write goes through the safe-write path.
- **Every command by name** — `:` (or `ctrl+p`) fuzzy-completes over the whole
  command registry with each command's key binding beside it, so the keymap
  teaches itself while you use it.

Not there yet: undo, tagging, delete, saved searches and visual mode. Bindings
for them exist and say so when pressed — see [`TODO.md`](TODO.md).

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
| `themes/*.tcss` | colours; pick one with `[ui] theme`                  |

The shipped defaults in [`src/ptui/defaults/`](src/ptui/defaults) are the
reference — copy and edit. Values papis already owns (library paths, `editor`,
`opentool`) come from papis config unless you override them.

## Keys

`j`/`k` move · `o` open · `/` filter · `s` search · `space` mark · `S` sort ·
`a` add · `i` add from inbox · `E` edit `info.yaml` · `q` quit.

Prefixes are namespaces, and hold one to see its menu: `g` go · `f` files ·
`y` yank · `c` change · `m` marks · `z` layout · `d` delete · `\` admin.
So `y y` yanks a `\cite{…}`, `f r` files a PDF where it belongs.

**[KEYS.md](KEYS.md) lists every binding** — key, command name and what it does,
per mode, generated from the shipped keymap. `?` shows the same table inside the
app, for whichever mode you are in.

## Development

```sh
uv sync
uv run pytest
uv run ruff check --fix && uv run ruff format
```

Design contract: [`SPEC.md`](SPEC.md). Agent notes: [`CLAUDE.md`](CLAUDE.md).

## License

MIT
