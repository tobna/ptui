"""Edges of command dispatch: the `:` line's argument parsing, and the guards
that keep a failing command, prompt, picker or worker from ending the session."""

import pytest

from ptui import actions, commands


@pytest.mark.parametrize(
    ("name", "text", "want"),
    [
        ("doc.set", 'title "Two words here"', {"key": "title", "value": "Two words here"}),
        ("doc.set", 'note ""', {"key": "note", "value": ""}),  # an empty value clears the field
        ("doc.set", "title it\\'s", {"key": "title", "value": "it's"}),
        ("doc.open", "2", {"which": 2}),  # `int | None`
        ("nav.down", "-2", {"count": -2}),  # a negative number is not a flag
        ("pane.resize", "-0.1", {"delta": -0.1}),
        *[("visual.start", word, {"unmark": True}) for word in ("TRUE", "yes", "on", "1")],
        *[("visual.start", word, {"unmark": False}) for word in ("false", "no", "0", "off")],
    ],
)
def test_parse_args(name, text, want):
    assert commands.parse_args(name, text) == want


@pytest.mark.parametrize(
    ("name", "text", "match"),
    [
        ("doc.tag", "ml cv", "at most 1"),  # two tags are one quoted argument
        ("doc.set", '"unclosed', "quotation"),
        ("lib.empty_trash", "1.5", "invalid literal"),
        ("pane.resize", "wide", "could not convert"),
    ],
)
def test_a_bad_line_raises_value_error(name, text, match):
    with pytest.raises(ValueError, match=match):
        commands.parse_args(name, text)


def test_signature_shows_each_arguments_type_and_default():
    assert commands.signature("config.set") == "<key: str> <value: str>"
    assert commands.signature("files.reorder") == "[index: int] [to: int = 1]"
    assert commands.signature("mark.all_filtered") == "[unmark: bool = false]"
    assert commands.signature("log.scroll") == "[up: bool = false] [lines: int = 0] [end: bool = false]"
    assert commands.signature("doctor.run") == "[checks: str] [current: bool = false]"  # "" says nothing


def test_an_unregistered_command_still_logs_not_implemented(app, monkeypatch):
    logged = []
    monkeypatch.setattr(app, "log_line", logged.append)
    app.run_command("no.such")
    assert logged == ["[yellow]no.such is not implemented yet[/]"]


async def test_a_command_that_fails_from_a_prompt_does_not_end_the_session(app, monkeypatch):
    """Keys go through run_command, which guards; a submitted prompt did not."""
    from conftest import press, settle

    def broken(_app, kind, value):
        raise ValueError(f"{kind}: {value}")

    async with app.run_test() as pilot:
        await settle(pilot)
        monkeypatch.setattr(actions, "prompt_result", broken)
        await press(pilot, "g", "r", *"nobody", "enter")
        assert app.is_running


async def test_a_picker_answer_that_raises_does_not_end_the_session(app):
    """What runs after `enter` in a picker is a screen callback, which no prompt guard covers."""
    from conftest import press, settle

    from ptui import ui

    def broken(value, _invert):
        raise ValueError(value)

    async with app.run_test() as pilot:
        await settle(pilot)
        ui.pick(app, [ui.Item(label="boom", value="boom")], title="Broken")(broken)
        await settle(pilot)
        await press(pilot, "enter")
        await settle(pilot)
        assert app.is_running


def _errors() -> list[str]:
    """loguru's ERROR records from here on. Added after the app mounts: its
    logging setup removes every sink there is."""
    from loguru import logger

    lines: list[str] = []
    logger.add(lambda message: lines.append(message.record["message"]), level="ERROR")
    return lines


def _boom(*_args, **_kwargs):
    raise ValueError("boom")


def _doctor_scan(app, monkeypatch):
    from ptui import doctor

    monkeypatch.setattr(doctor, "scan", _boom)
    app.scan_library()


def _doctor_recheck(app, monkeypatch):
    from ptui import doctor

    monkeypatch.setattr(doctor, "recheck", _boom)
    app.recheck()


def _fetch(app, monkeypatch):
    from ptui import fetch

    monkeypatch.setattr(fetch, "fetch", lambda _source, _uri: ({}, []))
    monkeypatch.setattr(actions, "_fetched", _boom)  # called back from the thread
    actions._import(app, "arxiv", "2607.00687")


def _refresh(app, monkeypatch):
    from types import SimpleNamespace

    from ptui import fetch

    monkeypatch.setattr(fetch, "refetch", lambda _importer: {})
    monkeypatch.setattr(actions, "_refresh_merge", _boom)
    actions._refresh_fetch(app, app.current, SimpleNamespace(name="doi"))


def _download(app, monkeypatch):
    import papis.paths

    monkeypatch.setattr(papis.paths, "download_remote_files", lambda urls: ["/nonexistent/paper.pdf"])
    monkeypatch.setattr(actions, "_attach", _boom)
    actions.files_attach(app, "https://example.org/paper.pdf")


@pytest.mark.parametrize("start", [_doctor_scan, _doctor_recheck, _fetch, _refresh, _download])
async def test_a_worker_that_raises_is_logged_and_the_session_stays_up(app, monkeypatch, start):
    """A Textual worker that raises ends the app unless told otherwise."""
    from contextlib import suppress

    from conftest import settle
    from textual.worker import WorkerFailed

    async with app.run_test() as pilot:
        await settle(pilot)
        errors = _errors()
        start(app, monkeypatch)
        with suppress(WorkerFailed):  # the wait re-raises what the worker raised
            await app.workers.wait_for_complete()
        await settle(pilot)
        assert app.is_running
        assert any(line.endswith("failed: boom") for line in errors)


async def test_a_picker_preview_that_raises_is_logged_and_the_session_stays_up(app):
    """The preview runs on every highlight — the theme picker's live theme."""
    from conftest import press, settle

    from ptui import ui

    async with app.run_test() as pilot:
        await settle(pilot)
        errors = _errors()
        items = [ui.Item(label="a", value="a"), ui.Item(label="b", value="b")]
        ui.pick(app, items, title="Theme", preview=_boom)(lambda _value, _invert: None)
        await settle(pilot)
        await press(pilot, "down")
        await settle(pilot)
        assert app.is_running
        assert "Theme preview failed: boom" in errors
