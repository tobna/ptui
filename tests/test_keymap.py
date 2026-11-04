"""Keymap loading, the prefix invariant and the command registry."""

import pytest

from ptui import commands, keymap


def test_shipped_keymap_has_no_prefix_conflicts():
    """Shipped keymap has no prefix conflicts."""
    assert keymap.load(keymap.DEFAULTS_DIR / "nope.toml").conflicts() == []


def test_prefix_conflict_is_reported(tmp_path):
    """Prefix conflict is reported.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "keys.toml").write_text('[modes.list]\n"g" = { cmd = "app.quit" }\n"g g" = { cmd = "nav.top" }\n')
    conflicts = keymap.load(tmp_path / "keys.toml").conflicts()
    # the shipped `g n`, `g s`, … and the global `g o` give way; the user's own two cannot
    assert conflicts == ["[modes.list] 'g' shadows 'g g' (app.quit vs nav.top)"]


def _user(tmp_path, text):
    """Write a user keys.toml and load it over the shipped one.

    Args:
        tmp_path: pytest's per-test temporary folder.
        text: The TOML.

    Returns:
        The merged `Keymap`.
    """
    (tmp_path / "keys.toml").write_text(text)
    return keymap.load(tmp_path / "keys.toml")


def test_user_bindings_merge_per_key_over_the_shipped_ones(tmp_path):
    """A user table used to replace the shipped one, so swapping two keys meant copying every binding.

    Each user key now drops only what it collides with.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    key_bindings = _user(tmp_path, '[modes.list]\n"o" = { cmd = "doc.open_folder" }\n"O" = { cmd = "doc.open" }\n')
    assert key_bindings.lookup("list", ("o",)).command == "doc.open_folder"
    assert key_bindings.lookup("list", ("O",)).command == "doc.open"
    assert key_bindings.lookup("list", ("j",)).command == "nav.down"  # everything else is still shipped
    assert key_bindings.lookup("list", ("enter",)).command == "doc.open"  # a command bound anew keeps its keys
    assert key_bindings.lookup("files", ("o",)).command == "doc.open"  # other modes are untouched
    assert key_bindings.option("which_key") is True
    assert key_bindings.conflicts() == []


def test_a_user_key_takes_a_shipped_prefix_and_its_namespace_with_it(tmp_path):
    """A user key takes a shipped prefix and its namespace with it.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    key_bindings = _user(tmp_path, '[modes.list]\n"y" = { cmd = "export.bibtex" }\n"r x" = { cmd = "app.reload" }\n')
    assert key_bindings.lookup("list", ("y",)).command == "export.bibtex"
    assert key_bindings.under_prefix("list", ("y",)) == []
    assert key_bindings.lookup("list", ("r",)) is None  # `r x` makes `r` a prefix
    assert key_bindings.lookup("list", ("r", "x")).command == "app.reload"
    assert key_bindings.lookup("list", ("f", "r")).command == "files.relocate"  # other namespaces stay
    assert key_bindings.conflicts() == []


def test_a_user_pane_key_beats_a_shipped_global_chord_in_that_pane_only(tmp_path):
    """A user pane key beats a shipped global chord in that pane only.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    key_bindings = _user(tmp_path, '[modes.info]\n"g" = { cmd = "nav.top" }\n')
    assert key_bindings.lookup("info", ("g", "o")) is None
    assert key_bindings.lookup("files", ("g", "o")).command == "app.log"
    assert key_bindings.conflicts() == []


def test_a_pane_key_beats_a_global_chord_it_shadows_in_that_pane_only(tmp_path):
    """A pane key beats a global chord it shadows in that pane only.

    Within one file, too: a pane binding `t` next to the global `t y` used to
    be a conflict that refused to start.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    key_bindings = _user(
        tmp_path,
        '[global]\n"t y" = { cmd = "app.reload" }\n"w" = { cmd = "app.reload", args = { rescan = true } }\n'
        '[modes.info]\n"t" = { cmd = "nav.top" }\n"w q" = { cmd = "nav.bottom" }\n',
    )
    assert key_bindings.lookup("info", ("t",)).command == "nav.top"
    assert key_bindings.lookup("info", ("t", "y")) is None
    assert key_bindings.lookup("info", ("w",)) is None  # the other way round: the pane's chord takes the global key
    assert key_bindings.lookup("info", ("w", "q")).command == "nav.bottom"
    assert key_bindings.lookup("list", ("t", "y")).command == "app.reload"
    assert key_bindings.lookup("list", ("w",)).args == {"rescan": True}
    assert key_bindings.conflicts() == []


def test_binding_a_command_to_a_new_key_keeps_its_shipped_keys(tmp_path):
    """A user binding always adds.

    `q` (the only quit key), `j` and `down`, `:` and `ctrl+p` all stay. Removing one is `"key" =
    false`.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    key_bindings = _user(
        tmp_path,
        '[global]\n"Q" = { cmd = "app.quit" }\n"w" = { cmd = "cmdline.open" }\n'
        '[modes.list]\n"n" = { cmd = "nav.down" }\n"t" = { cmd = "pane.focus", args = { pane = "log" } }\n',
    )
    for mode in keymap.PANES:
        assert key_bindings.lookup(mode, ("Q",)).command == "app.quit"
        assert key_bindings.lookup(mode, ("q",)).command == "app.quit"
        assert key_bindings.lookup(mode, ("w",)).command == "cmdline.open"
        assert key_bindings.lookup(mode, (":",)).command == "cmdline.open"
        assert key_bindings.lookup(mode, ("ctrl+p",)).command == "cmdline.open"
    assert [key_bindings.lookup("list", (key,)).command for key in ("n", "j", "down")] == ["nav.down"] * 3
    assert key_bindings.lookup("list", ("4",)).args == {"pane": "log"}


def test_false_unbinds_a_shipped_key(tmp_path):
    """False unbinds a shipped key.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    key_bindings = _user(tmp_path, '[global]\n"u" = false\n[modes.list]\n"b" = false\n"g" = false\n')
    assert key_bindings.lookup("list", ("b",)) is None
    assert key_bindings.under_prefix("list", ("g",)) == []  # a prefix takes its namespace, `g o` included
    assert key_bindings.lookup("info", ("g", "o")).command == "app.log"
    for mode in keymap.PANES:
        assert key_bindings.lookup(mode, ("u",)) is None
    assert key_bindings.conflicts() == []


def test_global_keys_reach_every_pane_but_not_the_picker(tmp_path):
    """Global keys reach every pane but not the picker.

    A mode used to be its own table, so a pane whose table lacked the pane keys
    — the log, once; any mode a user redefines — had no way out but escape.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "keys.toml").write_text('[modes.info]\n"x" = { cmd = "app.quit" }\n"u" = { cmd = "nav.down" }\n')
    key_bindings = keymap.load(tmp_path / "keys.toml")
    for mode in keymap.PANES:
        assert key_bindings.lookup(mode, ("tab",)).command == "pane.cycle"
        assert key_bindings.lookup(mode, ("g", "o")).command == "app.log"
    assert key_bindings.lookup("info", ("u",)).command == "nav.down"  # the pane's own table wins
    assert key_bindings.lookup("files", ("u",)).command == "app.undo"
    assert key_bindings.lookup("picker", ("u",)) is None  # letters are filter text there
    assert key_bindings.lookup("picker", ("tab",)) is None


def test_chords_and_lookup():
    """The shipped keymap resolves chords, prefixes and commands."""
    key_bindings = keymap.load(keymap.DEFAULTS_DIR / "nope.toml")
    assert key_bindings.lookup("list", ("g", "g")).command == "nav.top"
    assert key_bindings.is_prefix("list", ("g",))
    assert not key_bindings.is_prefix("list", ("j",))
    assert [binding.keys for binding in key_bindings.under_prefix("list", ("y",))] == [
        "y b",
        "y e",
        "y p",
        "y u",
        "y y",
    ]
    assert key_bindings.for_command("list", "app.quit") == "q"


@pytest.mark.parametrize(
    ("key", "char", "want"),
    [
        ("j", "j", "j"),
        ("space", " ", "space"),
        ("ctrl+d", None, "ctrl+d"),
        ("question_mark", "?", "?"),
    ],
)
def test_normalize(key, char, want):
    """`normalize` gives a Textual key event its keys.toml token.

    Args:
        key: Parametrized key name.
        char: Parametrized printable character, or `None`.
        want: Parametrized expected token.
    """
    assert keymap.normalize(key, char) == want


def test_registry_dispatch():
    """`commands.run` calls the handler with the app and its arguments."""
    calls = []
    commands.command("test.echo", "echo")(lambda app, value=1: calls.append((app, value)))
    commands.run("test.echo", "app", {"value": 2})
    assert calls == [("app", 2)]
    del commands.REGISTRY["test.echo"]
