import pytest

from ptui import commands, keymap


def test_shipped_keymap_has_no_prefix_conflicts():
    assert keymap.load(keymap.DEFAULTS_DIR / "nope.toml").conflicts() == []


def test_prefix_conflict_is_reported(tmp_path):
    (tmp_path / "keys.toml").write_text('[modes.list]\n"g" = { cmd = "app.quit" }\n"g g" = { cmd = "nav.top" }\n')
    conflicts = keymap.load(tmp_path / "keys.toml").conflicts()
    # the shipped `g n`, `g s`, … and the global `g o` give way; the user's own two cannot
    assert conflicts == ["[modes.list] 'g' shadows 'g g' (app.quit vs nav.top)"]


def _user(tmp_path, text):
    (tmp_path / "keys.toml").write_text(text)
    return keymap.load(tmp_path / "keys.toml")


def test_user_bindings_merge_per_key_over_the_shipped_ones(tmp_path):
    """A user table used to replace the shipped one, so swapping two keys meant
    copying every binding. Each user key now drops only what it collides with."""
    km = _user(tmp_path, '[modes.list]\n"o" = { cmd = "doc.open_folder" }\n"O" = { cmd = "doc.open" }\n')
    assert km.lookup("list", ("o",)).cmd == "doc.open_folder"
    assert km.lookup("list", ("O",)).cmd == "doc.open"
    assert km.lookup("list", ("j",)).cmd == "nav.down"  # everything else is still shipped
    assert km.lookup("list", ("enter",)).cmd == "doc.open"  # an alias of `o`: it stays
    assert km.lookup("files", ("o",)).cmd == "doc.open"  # other modes are untouched
    assert km.option("which_key") is True
    assert km.conflicts() == []


def test_a_user_key_takes_a_shipped_prefix_and_its_namespace_with_it(tmp_path):
    km = _user(tmp_path, '[modes.list]\n"y" = { cmd = "export.bibtex" }\n"r x" = { cmd = "app.reload" }\n')
    assert km.lookup("list", ("y",)).cmd == "export.bibtex"
    assert km.under_prefix("list", ("y",)) == []
    assert km.lookup("list", ("r",)) is None  # `r x` makes `r` a prefix
    assert km.lookup("list", ("r", "x")).cmd == "app.reload"
    assert km.lookup("list", ("f", "r")).cmd == "files.relocate"  # other namespaces stay
    assert km.conflicts() == []


def test_a_user_pane_key_beats_a_shipped_global_chord_in_that_pane_only(tmp_path):
    km = _user(tmp_path, '[modes.info]\n"g" = { cmd = "nav.top" }\n')
    assert km.lookup("info", ("g", "o")) is None
    assert km.lookup("files", ("g", "o")).cmd == "app.log"
    assert km.conflicts() == []


def test_a_pane_key_beats_a_global_chord_it_shadows_in_that_pane_only(tmp_path):
    """Within one file, too: a pane binding `t` next to the global `t y` used to
    be a conflict that refused to start."""
    km = _user(
        tmp_path,
        '[global]\n"t y" = { cmd = "app.reload" }\n"w" = { cmd = "app.reload", args = { rescan = true } }\n'
        '[modes.info]\n"t" = { cmd = "nav.top" }\n"w q" = { cmd = "nav.bottom" }\n',
    )
    assert km.lookup("info", ("t",)).cmd == "nav.top"
    assert km.lookup("info", ("t", "y")) is None
    assert km.lookup("info", ("w",)) is None  # the other way round: the pane's chord takes the global key
    assert km.lookup("info", ("w", "q")).cmd == "nav.bottom"
    assert km.lookup("list", ("t", "y")).cmd == "app.reload"
    assert km.lookup("list", ("w",)).args == {"rescan": True}
    assert km.conflicts() == []


def test_rebinding_a_command_moves_its_only_shipped_key(tmp_path):
    km = _user(
        tmp_path,
        '[global]\n"Q" = { cmd = "app.quit" }\n[modes.list]\n"x" = { cmd = "pane.focus", args = { pane = "log" } }\n',
    )
    for mode in keymap.PANES:
        assert km.lookup(mode, ("Q",)).cmd == "app.quit"
        assert km.lookup(mode, ("q",)) is None
    assert km.lookup("list", ("4",)) is None  # the same command with the same args
    assert km.lookup("list", ("3",)).args == {"pane": "files"}  # different args: another binding
    assert km.lookup("info", ("4",)).cmd == "pane.focus"  # x is the list's only


def test_rebinding_a_command_with_several_shipped_keys_keeps_them_all(tmp_path):
    """`j` and `down`, `:` and `ctrl+p` are aliases: rebinding the command adds a key."""
    km = _user(tmp_path, '[global]\n"x" = { cmd = "cmdline.open" }\n[modes.list]\n"n" = { cmd = "nav.down" }\n')
    assert km.lookup("list", ("n",)).cmd == "nav.down"
    assert km.lookup("list", ("j",)).cmd == "nav.down"
    assert km.lookup("list", ("down",)).cmd == "nav.down"
    for mode in keymap.PANES:
        assert km.lookup(mode, ("x",)).cmd == "cmdline.open"
        assert km.lookup(mode, (":",)).cmd == "cmdline.open"
        assert km.lookup(mode, ("ctrl+p",)).cmd == "cmdline.open"


def test_false_unbinds_a_shipped_key(tmp_path):
    km = _user(tmp_path, '[global]\n"u" = false\n[modes.list]\n"b" = false\n"g" = false\n')
    assert km.lookup("list", ("b",)) is None
    assert km.under_prefix("list", ("g",)) == []  # a prefix takes its namespace, `g o` included
    assert km.lookup("info", ("g", "o")).cmd == "app.log"
    for mode in keymap.PANES:
        assert km.lookup(mode, ("u",)) is None
    assert km.conflicts() == []


def test_global_keys_reach_every_pane_but_not_the_picker(tmp_path):
    """A mode used to be its own table, so a pane whose table lacked the pane keys
    — the log, once; any mode a user redefines — had no way out but escape."""
    (tmp_path / "keys.toml").write_text('[modes.info]\n"x" = { cmd = "app.quit" }\n"u" = { cmd = "nav.down" }\n')
    km = keymap.load(tmp_path / "keys.toml")
    for mode in keymap.PANES:
        assert km.lookup(mode, ("tab",)).cmd == "pane.cycle"
        assert km.lookup(mode, ("g", "o")).cmd == "app.log"
    assert km.lookup("info", ("u",)).cmd == "nav.down"  # the pane's own table wins
    assert km.lookup("files", ("u",)).cmd == "app.undo"
    assert km.lookup("picker", ("u",)) is None  # letters are filter text there
    assert km.lookup("picker", ("tab",)) is None


def test_chords_and_lookup():
    km = keymap.load(keymap.DEFAULTS_DIR / "nope.toml")
    assert km.lookup("list", ("g", "g")).cmd == "nav.top"
    assert km.is_prefix("list", ("g",))
    assert not km.is_prefix("list", ("j",))
    assert [b.keys for b in km.under_prefix("list", ("y",))] == ["y b", "y e", "y p", "y u", "y y"]
    assert km.for_command("list", "app.quit") == "q"


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
    assert keymap.normalize(key, char) == want


def test_registry_dispatch():
    calls = []
    commands.command("test.echo", "echo")(lambda app, x=1: calls.append((app, x)))
    commands.run("test.echo", "app", {"x": 2})
    assert calls == [("app", 2)]
    del commands.REGISTRY["test.echo"]
