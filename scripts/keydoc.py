"""Generate `KEYS.md` from the shipped keymap and the command registry.

The keymap is already the single source of truth for the app; this makes it the
source of truth for the documentation too, so the table cannot drift from what
the keys actually do. `tests/test_docs.py` fails when the file is stale.

    uv run python scripts/keydoc.py          # rewrite KEYS.md
    uv run python scripts/keydoc.py --check  # print it instead
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

from ptui import actions  # noqa: F401  — importing fills the registry
from ptui.commands import REGISTRY
from ptui.config import DEFAULTS_DIR

ROOT = Path(__file__).resolve().parent.parent

MODE_INTROS = {
    "list": "The document list — the mode ptui starts in, and the one every other mode falls back to.",
    "files": "The files pane. Not built yet, so these bindings only log.",
    "info": "The info pane, focused with `2` or `tab`.",
    "picker": "Any modal picker (`S`, `f o`, `g l`, `?`). The filter box has focus, so motions cannot be letters.",
}

OPTION_DOCS = {
    "which_key": "Show the bindings under a prefix after a pause.",
    "which_key_delay_ms": "How long that pause is.",
    "leader": "The leader key for rare and administrative commands.",
    "hint_bar": "Show context-relevant bindings on the bottom row.",
    "hint_bar_max": "How many hints fit there.",
    "show_keys_in_cmdline": "Show each command's binding beside it in the `:` command line.",
    "escape_chain": "What `escape` cancels in the list mode, first applicable wins. "
    "Outside the list mode escape always returns to the list.",
}


def render() -> str:
    data = tomllib.loads((DEFAULTS_DIR / "keys.toml").read_text())
    out = [
        "# Keys",
        "",
        "Every binding ptui ships with, generated from `src/ptui/defaults/keys.toml`",
        "and the command registry — run `uv run python scripts/keydoc.py` after changing",
        "either. Copy the file to `$XDG_CONFIG_HOME/papis/ptui/keys.toml` to override:",
        "a mode you define replaces ours wholesale, so a shipped binding can be removed.",
        "",
        "`escape` always leaves the current mode and `?` always opens the help, in every",
        "mode, whatever the tables below say — the dispatcher guarantees both so no pane",
        "can trap the keyboard.",
        "",
        "Commands marked *(not implemented)* are bound on purpose: they log a notice",
        "instead of doing anything, and the binding is already in place for when they land.",
        "",
    ]

    for mode, bindings in data.get("modes", {}).items():
        out += [f"## `[modes.{mode}]`", ""]
        if mode in MODE_INTROS:
            out += [MODE_INTROS[mode], ""]
        out += ["| Keys | Command | Does what |", "| --- | --- | --- |"]
        for keys, spec in sorted(bindings.items(), key=lambda kv: (kv[0][0].casefold(), kv[0])):
            cmd = spec["cmd"]
            registered = REGISTRY.get(cmd)
            desc = spec.get("desc") or (registered.desc if registered else "—")
            args = " ".join(f"{k}={v}" for k, v in spec.get("args", {}).items())
            note = "" if registered else " *(not implemented)*"
            out.append(f"| `{keys}` | `{cmd}`{f' {args}' if args else ''} | {desc}{note} |")
        out.append("")

    out += ["## `[options]`", "", "| Option | Default | Does what |", "| --- | --- | --- |"]
    for option, value in data.get("options", {}).items():
        # json, not repr: the file documents TOML, where it is `true`, not `True`.
        out.append(f"| `{option}` | `{json.dumps(value)}` | {OPTION_DOCS.get(option, '')} |")
    out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        print(text)
    else:
        (ROOT / "KEYS.md").write_text(text)
        print(f"wrote {ROOT / 'KEYS.md'}")
