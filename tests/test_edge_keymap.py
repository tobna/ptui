"""Edges of key normalisation and the prefix check."""

import pytest

from ptui import keymap


@pytest.mark.parametrize(
    ("key", "char", "want"),
    [
        ("enter", "\r", "enter"),  # Textual sends a control character with these
        ("tab", "\t", "tab"),
        ("escape", "\x1b", "escape"),
        ("backspace", "\x08", "backspace"),
        ("A", "A", "A"),  # shift is in the character, not a modifier
        ("backslash", "\\", "\\"),
        ("ctrl+r", "\x12", "ctrl+r"),
    ],
)
def test_normalize_control_characters_use_the_key_name(key, char, want):
    assert keymap.normalize(key, char) == want


def test_conflicts_are_per_mode_and_reach_every_longer_chord():
    """Built directly, not through `load`, so how keys.toml merges cannot change the answer."""

    def mode(*chords):
        return {chord: keymap.Binding(chord, "nav.top", {}, "") for chord in chords}

    km = keymap.Keymap({"list": mode(("g",), ("g", "g"), ("g", "g", "g")), "files": mode(("g",))}, {}, ())
    conflicts = km.conflicts()
    assert len(conflicts) == 3  # g/gg, g/ggg, gg/ggg
    assert all(c.startswith("[modes.list]") for c in conflicts)  # alone in its mode, `g` is fine
