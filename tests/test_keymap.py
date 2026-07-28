import pytest

from ptui import commands, keymap


def test_shipped_keymap_has_no_prefix_conflicts():
    assert keymap.load(keymap.DEFAULTS_DIR / "nope.toml").conflicts() == []


def test_prefix_conflict_is_reported(tmp_path):
    (tmp_path / "keys.toml").write_text(
        '[modes.list]\n"g" = { cmd = "app.quit" }\n"g g" = { cmd = "nav.top" }\n'
    )
    conflicts = keymap.load(tmp_path / "keys.toml").conflicts()
    assert len(conflicts) == 1
    assert "'g' shadows 'g g'" in conflicts[0]


def test_user_mode_replaces_shipped_mode(tmp_path):
    (tmp_path / "keys.toml").write_text('[modes.list]\n"x" = { cmd = "app.quit" }\n')
    km = keymap.load(tmp_path / "keys.toml")
    assert set(km.modes["list"]) == {("x",)}
    assert km.modes["files"]  # untouched modes survive
    assert km.option("which_key") is True


def test_chords_and_lookup():
    km = keymap.load(keymap.DEFAULTS_DIR / "nope.toml")
    assert km.lookup("list", ("g", "g")).cmd == "nav.top"
    assert km.is_prefix("list", ("g",))
    assert not km.is_prefix("list", ("j",))
    assert [b.keys for b in km.under_prefix("list", ("y",))] == ["y b", "y p", "y u", "y y"]
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
