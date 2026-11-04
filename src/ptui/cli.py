"""Entry point. Also registered as `papis ptui`, so it must be a click command."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import click

from ptui import config, keymap, undo


def _library_dir(name: str | None) -> Path | None:
    """Find the folder of the library ptui is about to open.

    Args:
        name: Library name, or `None` for papis's current library.

    Returns:
        The library's folder, or `None` for a name papis does not know, which the app reports once it is
        up.
    """
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
    """Browse a papis library in a terminal UI.

    \f
    This docstring doubles as click's `--help` text; the form feed above ends what click shows.

    Args:
        lib: Name of the papis library to open, or `None` for the configured one.
        query: Words of the startup query, joined with spaces; empty keeps the configured query.
    """  # noqa: D301
    try:
        configuration = config.load()
        key_bindings = keymap.load()
    except ValueError as error:  # a syntax error or a binding with no command, naming its file
        click.echo(f"ptui: {error}", err=True)
        sys.exit(1)

    # SPEC: a shadowed binding is invisible until someone wonders why `o` feels
    # slow, so refuse to start rather than start subtly wrong.
    conflicts = key_bindings.conflicts()
    if conflicts:
        click.echo("keys.toml has prefix conflicts:", err=True)
        for conflict in conflicts:
            click.echo(f"  {conflict}", err=True)
        sys.exit(1)

    # `undo.strategy = "git"` on a library outside git cannot work: say so the
    # way config repair does, and use the trash instead.
    where = _library_dir(lib or configuration.get("general.library"))
    if where is not None and (why := undo.require_git(configuration, where)):
        configuration = dataclasses.replace(configuration, invalid=(*configuration.invalid, why))

    from ptui.app import PtuiApp  # imported late: pulls in Textual

    app = PtuiApp(configuration, key_bindings)
    if lib:
        app.config.data["general"]["library"] = lib
    if query:
        app.scope_query = " ".join(query)
    app.run(mouse=bool(configuration.get("ui.mouse", True)))


if __name__ == "__main__":
    main()
