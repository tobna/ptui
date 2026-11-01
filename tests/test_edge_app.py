"""Edges of the app's command dispatch."""

from conftest import settle

from ptui import commands


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
