<h1 align="center">ptui</h1>

<p align="center">
  A fast, keyboard-driven terminal UI for
  <a href="https://github.com/papis/papis">papis</a>.<br>
  <em>Beta. Not on PyPI yet.</em>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.13%2B-blue">
  <img alt="Built with Textual" src="https://img.shields.io/badge/built%20with-Textual-5a5aff">
  <img alt="papis" src="https://img.shields.io/badge/papis-0.16.x-b8283c">
</p>

---

papis keeps your library as plain folders, one `info.yaml` per paper, and its
command line is good for scripts. ptui is for working in that library by hand:
find a paper in a few keystrokes, read the abstract, open the PDF, tag it, copy
its BibTeX. It reads and writes the same files papis does, so the `papis`
command keeps working next to it.

The [keys](KEYS.md) are vim-like, and you can start without learning them. Press a prefix
such as `g` or `c` and a menu shows what can follow. `?` lists every key of the
pane you are in, and `:` finds any command by name.

## Install

You need Python 3.13 or newer and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/tobna/ptui
cd ptui
uv sync
uv run ptui
```

`uv tool install --editable .` gives you a `ptui` command that works in any
directory. If you have no papis library yet, `uv run papis init
/path/to/library` creates one.

<details>
<summary>Running it as <code>papis ptui</code></summary>

papis finds ptui only when both are installed in the same environment. From the
checkout, `uv run papis ptui` works. If your papis came from pipx, add ptui to
it:

```sh
pipx inject --editable --include-apps papis /path/to/ptui
```

</details>

## What it does

**Search**<br>
Two layers. `s` asks papis for the scope, and `/` filters it as you type
(`vision -survey year:>2023`). The status bar shows both, so you can tell why a
paper is not in the list.

<details>
<summary>Filter terms, sorting and saved searches</summary>

In `/`, every term has to match: `-word` excludes, `"two words"` is a phrase,
`field:value` looks in one field, and `year:>2023` or `year:2020..2024`
compares numbers. `a:`, `t:`, `y:` and `k:` are short for author, title, year
and tags, in `/` and in `s`. `escape` drops the filter.

`S` picks the sort and `ctrl+s` reverses it. `\ s` saves the scope and sort
under a name, and `g s` brings it back. In a search prompt, `up` recalls
earlier queries; type a few letters first to see only those that start with
them. `g r` jumps to a citekey, and you can paste a whole `\cite{key}`.

With `general.persist_state = true`, ptui starts where your last session in
that library ended.

</details>

<br>

**Marks**<br>
Marks belong to papers, so sorting and filtering keep them. A command acts on
the marked papers, or on the one under the cursor when nothing is marked.

<details>
<summary>Ranges, marking by query, and batch confirms</summary>

`space` marks and moves on. `v` marks everything the cursor passes until you
press `v` or `escape` again, like vim's visual mode, and `V` unmarks the same
way. `m a` marks the filtered list, `m q` marks by a query without changing
the view, `m i` inverts, `m c` clears, and `g m` shows only the marked papers.

Field edits, deletes, moves, `f r` and doctor fixes ask first when they would
change more than one paper, and the count includes marked papers the filter
hides.

</details>

<br>

**Editing**<br>
`c t` tags, `c s` sets the reading status, `c r` rates, and `c f` sets any
field. `e` opens `info.yaml` in your editor, and `E` a form over the common
fields.

<details>
<summary>Tags, field types, the editor and the form</summary>

`c t` adds tags and keeps the ones already there. Tags are separated by commas,
so `machine learning, cv` is two tags. `c T` removes tags. A rating goes from
0 to 5, and 0 clears it. ptui writes the type papis declares for a field, so
`year` stays a number and `tags` a list. An empty value removes the field.

If you save YAML in the editor that no longer parses, ptui says so and shows
the paper as it was until you fix the file. The form covers title, authors,
year, tags, citekey, DOI and URL, and `edit.mode = "structured"` swaps `e` and
`E`. `g n` opens the paper's notes.

</details>

<br>

**Updating from the source**<br>
`c u` fetches the paper again from its DOI, its arXiv id or a Crossref search.
It fills in the fields the paper lacks and asks about each field where the
source disagrees with what you have; `papis update --auto` would overwrite
them. A Crossref search lists its hits, so you pick the right paper first.

**Adding papers**<br>
`a` adds a paper from a file, your inbox, an arXiv id, a DOI, an ISBN, a URL, a
`.bib`, a Crossref search and more. A form shows what was found before
anything is written, and if the library already has the paper, ptui asks
first.

<details>
<summary>Every source, duplicates, and the date added</summary>

The sources are a file, your inbox (`~/Downloads` unless you set
`files.inbox`), a URL, a `.bib` file, an arXiv id, a DOI, an ISBN, a PubMed id,
a DBLP key, a Zenodo record, a Crossref search, a PDF to read a DOI or arXiv id
from, or an existing papis folder or library. `i` goes straight to the inbox,
and `:doc.add arxiv 2607.00687` skips both questions. An arXiv import brings
the PDF along.

ptui counts a paper as already there when the DOI, ISBN, arXiv id, URL or
title matches, and asks whether to go to it, add it anyway or cancel.

ptui stamps `time-added` on what it adds, so the default sort puts new papers
on top. papis stamps only papers added with `papis add`, so run
`:lib.backfill_dates` once to date the older ones by when their `info.yaml`
last changed.

</details>

<br>

**Merging, deleting and undo**<br>
`m m` merges duplicates into one paper and `d d` deletes, and `u` undoes
either. Deleted and merged-away folders wait in a trash for 30 days.

<details>
<summary>What a merge keeps, what undo covers, and the trash</summary>

A merge is for a paper you have twice, say from arXiv and from the
proceedings. You choose which citekey survives, each real disagreement is one
question, and every file is kept. The delete dialog has a checkbox for each
file; only files in a folder ptui manages start ticked. `:doc.move` moves
papers to another papis library.

`u` undoes and `ctrl+r` redoes, up to 50 steps in a session. That covers field
edits, the form, `c u`, everything in the files pane, `f n`, `f r`, doctor
fixes, deletes and merges. `f a`, edits made in your editor and `:doc.move`
cannot be undone.

The trash is `~/.local/share/ptui/trash`. At startup ptui deletes what has
been there more than 30 days and says so in the log, and `:lib.empty_trash`
empties it when you ask. If the library is a git repository,
`undo.strategy = "git"` makes each delete a commit that `u` reverts. Files
still go to the trash, since a PDF folder outside the library is not in the
repository.

</details>

<br>

**Files**<br>
`o` opens the PDF and `f a` attaches a file or URL. Placement rules decide
where files belong, and `f r` moves them there. `3` opens a pane with the
paper's files, to reorder, rename, repoint or detach them.

<details>
<summary>Opening, the files pane, and a rule that moves PDFs</summary>

`o` (or `enter`) opens the main file with papis's `opentool`, `f o` picks one
when there are several, `O` opens the folder and `b` the URL or DOI. `f a`
works as `papis addto` does.

In the files pane, `x` takes a file off the list and leaves it on disk, `K`
and `J` move it (the first one is the main file), `p` points a broken entry at
another file, `r` renames to papis's naming scheme, and `m` applies the
placement rules.

The rules are `[[files.rules]]` in `config.toml`, tried in order, and the first
match wins. The shipped ones leave every file where papis put it, so nothing
moves until you write a rule that does. This one keeps all PDFs in one folder
outside the library:

```toml
[files]
pdf_root = "~/papers"

[[files.rules]]
name = "pdfs to one folder"
match = ["*.pdf"]
op = "move"
dest = "{pdf_root}/{doc[ref]}.pdf"
path_style = "absolute"
```

A move never overwrites an existing file.

</details>

<br>

**The doctor**<br>
papis's doctor checks every paper in the background, and the list marks the
ones with problems. `! !` shows only those. Nothing is fixed until you ask:
`! f` fixes the paper, and `! o` one finding you pick.

**Copying and exporting**<br>
`y y` copies `\cite{key}`, `y b` the BibTeX, and `y e` exports in any format
papis has an exporter for. ptui uses `wl-copy`, `xclip` or `pbcopy` when one
is installed, and otherwise the terminal (OSC 52), which also works over SSH.

**Safe writes**<br>
Before each write, ptui checks that `info.yaml` has not changed since it read
it. If something else changed it (a sync, a script, papis itself), ptui
reloads that paper and asks you to do it again. Your comments and keys ptui
does not know survive the write, except with `f a`, which is `papis addto`.

**Looks**<br>
Themes, previewed as you move through `\ t`. Nerd-font icons, or plain
characters with `ui.icons = false`. On a narrow terminal the info pane moves
under the list.

<details>
<summary>Panes, columns and the mouse</summary>

There are four panes: the list (`1`), info (`2`), files (`3`) and the log
(`4`). `tab`, `h` and `l` move between them. `z z` switches between side by
side and stacked, `z i` hides the info pane, and `z +` and `z -` move the
split. The columns are papis format strings in `[[list.columns]]`, and
`list.row_height = 2` gives long titles a second line. `ui.mouse = false`
leaves the mouse to the terminal so you can select text.

</details>

## First steps

```sh
ptui                  # your default papis library
ptui -l work          # the library papis calls "work"
ptui tags:to-read     # start with a papis query as the scope
```

| Keys                         | What happens                                       |
| ---------------------------- | -------------------------------------------------- |
| `s` `a:nauen` `enter`        | ask papis for one author's papers                  |
| `/` `vision -survey` `enter` | keep those with "vision" and without "survey"      |
| `space` `space`              | mark two papers                                    |
| `c t` `to-read` `enter`      | tag both (ptui asks first, since it is two papers) |
| `y b`                        | copy their BibTeX                                  |
| `o`                          | open the PDF of the paper under the cursor         |

`q` quits. Every key is in [KEYS.md](KEYS.md).

## Configuration

ptui reads `config.toml` and `keys.toml` from the `ptui` folder in papis's
config folder, usually `~/.config/papis/ptui/`. Neither has to exist: write
only what you want to change. [CONFIG.md](CONFIG.md) lists every setting with
its default, and [KEYS.md](KEYS.md) every key binding and how yours combine
with the shipped ones.

## Development

```sh
uv sync
uv run pytest
uv run ruff check --fix && uv run ruff format
uv run python scripts/keydoc.py      # regenerate KEYS.md after changing keys.toml
uv run python scripts/configdoc.py   # regenerate CONFIG.md after changing config.toml
```

CI runs the tests on Python 3.13 for every push and pull request, and they
fail while KEYS.md or CONFIG.md is out of date. [SPEC.md](SPEC.md) says how
ptui is meant to behave, and [CLAUDE.md](CLAUDE.md) holds notes on the code
for coding agents.

## License

MIT, see [LICENSE](LICENSE).
