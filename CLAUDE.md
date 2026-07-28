# CLAUDE.md — working notes for agents

ptui is a [Textual](https://textual.textualize.io/) TUI for
[papis](https://github.com/papis/papis). `SPEC.md` is the design contract —
read it before changing behaviour; this file is the map of what actually
exists. `TODO.md` is the backlog from real use: **read it before starting
anything, and take work from the top of section A.**

## Golden rules

1. **Keep `CLAUDE.md` and `README.md` current.** Any change to structure,
   commands, config keys, or workflow updates both, in the same commit. Run
   `/sync-docs` (`.claude/commands/sync-docs.md`) if unsure whether they drifted.
2. **`SPEC.md` wins over intuition.** If the code must deviate, change the spec
   in the same commit and say why.
3. **Verify every papis API call against the installed version** (`uv run python -c ...`).
   The internal API moved between 0.14 and 0.15. Do not trust API names from
   `SPEC.md` or from memory.
4. **Never touch `info.yaml` outside the safe-write helper.** Papis caches;
   direct writes desync it. Unknown keys and comments must survive a write.
5. **Documents are identified by `papis_id`, never by folder path.**
6. Commit in standalone steps. No AI/Claude mentions in commit messages.

## Toolchain

`uv` for everything. Python 3.13+ (dev machine runs 3.14).

```sh
uv sync                 # install
uv run ptui             # launch (also available as `papis ptui`)
uv run pytest           # tests
uv run ruff check --fix && uv run ruff format

# look at the UI without a terminal — writes a PNG you can open
uv run python scripts/shot.py /tmp/shot.png --size 160x30 slash n a u e n
```

**Look at the screenshot before claiming a visual bug is fixed.** Pilot presses
real keys, so this exercises the actual app; several layout problems in
`TODO.md` were found this way rather than reported.

## Layout

```
SPEC.md              design contract — the source of truth for behaviour
src/ptui/
  cli.py             click entry point; refuses to start on keymap conflicts
  app.py             PtuiApp: widgets, key dispatch, the state commands mutate
  actions.py         every command implementation, one @command per function
  commands.py        the registry — keymaps, help and hints all derive from it
  keymap.py          keys.toml -> chords, prefix-conflict check, which-key data
  config.py          shipped defaults + per-key user overrides
  library.py         scope query, in-memory narrow, sorting, display text
  ui.py              SelectList — the one shared modal picker
  clip.py            clipboard: local tool, else OSC52
  place.py           file placement: atomic, idempotent, no-clobber
  safewrite.py       the only path that writes info.yaml
  defaults/          shipped config.toml, keys.toml, themes/*.tcss
tests/
```

Data flow: a keypress becomes a chord in `app.on_key`, resolves through
`keymap` to a name in `commands.REGISTRY`, and lands in `actions.py`. Adding a
command means adding one decorated function — help, the hint bar and which-key
pick it up for free. An unregistered command logs "not implemented yet" rather
than crashing, which is how the shipped keymap can bind post-v0 commands.

User config lives in `$XDG_CONFIG_HOME/papis/ptui/` and mirrors
`src/ptui/defaults/`. Anything papis already owns (library paths, `editor`,
`opentool`, `use-git`) is read from papis config; `config.toml` may override
per key.

## papis and Textual gotchas (learned the hard way)

- **`PAPIS_NP=0`** is set in `library.py`. Papis forks a process pool to build
  its cache; inside a TUI that copies the whole app and dies on Textual's
  redirected file descriptors (`ValueError: bad value(s) in fds_to_keep`).
- **Never name app state after a Textual property.** `App.visible` exists, so
  the narrowed list is `app.rows`. Check `hasattr(App, name)` before adding one.
- The `papis.command` entry point must be a **`click.Command`**, so `cli.main`
  is decorated with `@click.command`.
- Papis resolves the current library from lazily-loaded global state, so tests
  need the autouse `papis_lib` fixture (`tests/conftest.py`) or they depend on
  whoever runs them having a library named `papers`.
- Textual widgets are driven manually: `can_focus = False` everywhere except the
  prompt `Input`, so every key reaches `App.on_key` and the keymap owns dispatch.
- Do not shadow Textual internals: `SelectList._populate` is called that because
  `Widget._render` already exists and overriding it renders nothing.
- `Static.update` parses markup, and a destination path is full of
  `[doc[year]]`-shaped text — pass `markup=False` for anything path-shaped.
- `papis.format.format` **returns the unformatted pattern** (or raises deep in
  `string.Formatter`) when a key is missing; `default=""` does not save nested
  lookups like `{doc[author_list][0][family]}`. Check for a leftover `{`.

## Status

**v0 is feature-complete** against `SPEC.md` § "v0 scope" — two panes, scope
query + narrow filter, vim navigation, `doc.open`, `doc.edit_raw`,
`export.citekey`, marks, the add flow, `files.relocate`, safe-write, log pane,
`keymap.check`, which-key + hint bar. 47 tests, `uv run pytest`.

Built:

- config + keymap loading, command registry, prefix-conflict check
- safe `info.yaml` writes, `place()` file placement
- the app: list + info panes, scope query, debounced narrow, sorting, marks,
  which-key, hint bar, status bar, log pane
- verbs: `doc.open`, `doc.open_folder`, `doc.browse`, `doc.edit_raw`,
  `export.{citekey,path,url,bibtex}`, `files.relocate`
- `SelectList` modal + `sort.picker`, `files.open_pick`, `lib.switch`
- the add flow: inbox picker, metadata form, live destination preview, `place()`

Not built (deliberately, per SPEC "not in v0"): structured editor, undo, doctor,
saved searches, themes beyond the built-in one, picker entry point, reading
status, ratings. Also missing and *not* excluded by SPEC: `help.show`, the `:`
command line, `doc.notes`, `doc.delete`, state persistence
(`general.persist_state` is read but ignored). Unbound commands log
"not implemented yet".

First real session produced `TODO.md`. Known-bad right now: switching panes
strands the keyboard (no per-mode fallback to `[modes.list]`), Textual's own
command palette steals `ctrl+p`, the layout toggle does nothing, and `/` narrows
too loosely. Do not add features before `TODO.md` § A is clear.

## Conventions

- Follow the `python-rules` skill: f-strings, `pathlib`, `dataclass` records,
  `X | None`, ruff-clean.
- Non-trivial logic leaves one runnable check behind (`tests/test_*.py`, plain
  `assert`, no fixtures until two tests share setup).
- Deliberate shortcuts get a `# ponytail:` comment naming the ceiling.
