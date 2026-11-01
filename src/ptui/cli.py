"""Entry point. Also registered as `papis ptui`, so it must be a click command."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import click

from ptui import config, keymap, undo


def _library_dir(name: str | None) -> Path | None:
    """Where the library ptui is about to open lives; None for a name papis does
    not know, which the app reports once it is up."""
    import papis.config

    try:
        return Path(papis.config.get_lib_from_name(name or papis.config.get_lib_name()).path)
    except Exception:
        return None


@click.command("ptui")
@click.help_option("-h", "--help")
@click.option("-l", "--lib", "lib", default=None, help="Papis library to open.")
@click.argument("query", nargs=-1)
def main(lib: str | None, query: tuple[str, ...]) -> None:
    """Browse a papis library in a terminal UI."""
    try:
        cfg = config.load()
        km = keymap.load()
    except ValueError as exc:  # a syntax error or a binding with no command, naming its file
        click.echo(f"ptui: {exc}", err=True)
        sys.exit(1)

    # SPEC: a shadowed binding is invisible until someone wonders why `o` feels
    # slow, so refuse to start rather than start subtly wrong.
    conflicts = km.conflicts()
    if conflicts:
        click.echo("keys.toml has prefix conflicts:", err=True)
        for conflict in conflicts:
            click.echo(f"  {conflict}", err=True)
        sys.exit(1)

    # `undo.strategy = "git"` on a library outside git cannot work: say so the
    # way config repair does, and use the trash instead.
    where = _library_dir(lib or cfg.get("general.library"))
    if where is not None and (why := undo.require_git(cfg, where)):
        cfg = dataclasses.replace(cfg, invalid=(*cfg.invalid, why))

    from ptui.app import PtuiApp  # imported late: pulls in Textual

    app = PtuiApp(cfg, km)
    if lib:
        app.cfg.data["general"]["library"] = lib
    if query:
        app.scope_query = " ".join(query)
    app.run(mouse=bool(cfg.get("ui.mouse", True)))


if __name__ == "__main__":
    main()
