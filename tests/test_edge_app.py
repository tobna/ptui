"""Edges of the app's command dispatch."""

import pytest
from conftest import settle

from ptui import commands


@pytest.mark.xfail(
    strict=True,
    reason="BUG: PtuiApp.run_command catches every KeyError as `commands.run`'s unknown-name signal, "
    "so a KeyError raised inside a real command is logged as 'not implemented yet' and its traceback dropped",
)
async def test_a_keyerror_inside_a_command_is_reported_as_a_failure(app, monkeypatch):
    def broken(_app):
        return {}["files"]

    monkeypatch.setitem(commands.REGISTRY, "test.broken", commands.Command("test.broken", "broken", broken))
    async with app.run_test() as pilot:
        await settle(pilot)
        logged = []
        monkeypatch.setattr(app, "log_line", logged.append)
        app.run_command("test.broken")
        assert not any("not implemented" in message for message in logged)
