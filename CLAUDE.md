# CLAUDE.md — working notes for agents

ptui is a [Textual](https://textual.textualize.io/) TUI for
[papis](https://github.com/papis/papis). `SPEC.md` is the design contract —
read it before changing behaviour; this file is the map of what actually
exists.

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
```

## Layout

```
SPEC.md                     design contract — the source of truth for behaviour
src/ptui/
  defaults/                 shipped defaults, copied to the user config dir on
    config.toml             first run; also the documented example config
    keys.toml
    themes/ink-crimson.tcss
tests/
```

User config lives in `$XDG_CONFIG_HOME/papis/ptui/` and mirrors
`src/ptui/defaults/`. Anything papis already owns (library paths, `editor`,
`opentool`, `use-git`) is read from papis config; `config.toml` may override
per key.

## Status

v0 in progress. Scope per `SPEC.md` § "v0 scope":
two panes · scope query + narrow filter · vim navigation · `doc.open`,
`doc.edit_raw`, `export.citekey` · marks · add flow · `files.relocate` ·
safe-write · log pane · `keymap.check` · which-key + hint bar.

Built so far:

- project scaffolding, dependencies, shipped default config

## Conventions

- Follow the `python-rules` skill: f-strings, `pathlib`, `dataclass` records,
  `X | None`, ruff-clean.
- Non-trivial logic leaves one runnable check behind (`tests/test_*.py`, plain
  `assert`, no fixtures until two tests share setup).
- Deliberate shortcuts get a `# ponytail:` comment naming the ceiling.
