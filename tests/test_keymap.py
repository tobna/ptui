import pytest

from ptui import commands, keymap


def test_shipped_keymap_has_no_prefix_conflicts():
    assert keymap.load(keymap.DEFAULTS_DIR / "nope.toml").conflicts() == []


def test_prefix_conflict_is_reported(tmp_path):
    (tmp_path / "keys.toml").write_text('[modes.list]\n"g" = { cmd = "app.quit" }\n"g g" = { cmd = "nav.top" }\n')
    conflicts = keymap.load(tmp_path / "keys.toml").conflicts()
    # `g o` comes from [global]: it shadows that too, in the mode it lands in
    assert len(conflicts) == 2
    assert "[modes.list] 'g' shadows 'g g'" in conflicts[0]
    assert "[modes.list] 'g' shadows 'g o'" in conflicts[1]


def test_user_mode_replaces_shipped_mode(tmp_path):
    (tmp_path / "keys.toml").write_text('[modes.list]\n"x" = { cmd = "app.quit" }\n')
    km = keymap.load(tmp_path / "keys.toml")
    shared = set(keymap.load(tmp_path / "nope.toml").modes["log"]) - {("j",), ("k",), ("G",)}
    assert set(km.modes["list"]) - shared == {("x",)}
    assert km.lookup("list", ("o",)) is None  # the shipped list table is gone
    assert km.modes["files"]  # untouched modes survive
    assert km.option("which_key") is True


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
