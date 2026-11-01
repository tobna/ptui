"""Edges of the `:` line's argument parsing."""

import pytest

from ptui import actions, commands  # noqa: F401 — actions fills the registry


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
