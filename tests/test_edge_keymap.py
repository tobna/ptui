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
    r"""Normalize control characters use the key name.

    Args:
        key: Parametrized, such as `'enter'`.
        char: Parametrized, such as `'\r'`.
        want: Parametrized, such as `'enter'`.
    """
    assert keymap.normalize(key, char) == want


def test_conflicts_are_per_mode_and_reach_every_longer_chord():
    """Built directly, not through `load`, so how keys.toml merges cannot change the answer."""

    def mode(*chords):
        """Build a mode's table.

        Args:
            *chords: The chords to bind.

        Returns:
            Chord to a `Binding` of `nav.top`.
        """
        return {chord: keymap.Binding(chord, "nav.top", {}, "") for chord in chords}

    key_bindings = keymap.Keymap({"list": mode(("g",), ("g", "g"), ("g", "g", "g")), "files": mode(("g",))}, {}, ())
    conflicts = key_bindings.conflicts()
    assert len(conflicts) == 3  # g/gg, g/ggg, gg/ggg
    assert all(conflict.startswith("[modes.list]") for conflict in conflicts)  # alone in its mode, `g` is fine
