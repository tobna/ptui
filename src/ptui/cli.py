"""Entry point. Also registered as `papis ptui`, so it must be a click command."""

from __future__ import annotations

import sys

import click

from ptui import config, keymap


@click.command("ptui")
@click.help_option("-h", "--help")
@click.option("-l", "--lib", "lib", default=None, help="Papis library to open.")
@click.argument("query", nargs=-1)
def main(lib: str | None, query: tuple[str, ...]) -> None:
    """Browse a papis library in a terminal UI."""
    cfg = config.load()
    km = keymap.load()

    # SPEC: a shadowed binding is invisible until someone wonders why `o` feels
    # slow, so refuse to start rather than start subtly wrong.
    conflicts = km.conflicts()
    if conflicts:
        click.echo("keys.toml has prefix conflicts:", err=True)
        for conflict in conflicts:
            click.echo(f"  {conflict}", err=True)
        sys.exit(1)

    from ptui.app import PtuiApp  # imported late: pulls in Textual

    app = PtuiApp(cfg, km)
    if lib:
        app.cfg.data["general"]["library"] = lib
    if query:
        app.scope_query = " ".join(query)
    app.run()


if __name__ == "__main__":
    main()
