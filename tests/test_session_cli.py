"""Startup through the real entry point, `ptui.cli.main`.

As far as it goes without a terminal: everything up to `PtuiApp.run`, which is replaced.
"""

import pytest
from click.testing import CliRunner


@pytest.fixture
def started(monkeypatch):
    """Every app the command line would have run, instead of running it.

    Args:
        monkeypatch: pytest's monkeypatch fixture.

    Returns:
        A list receiving `(app, run's keyword arguments)` per app started.
    """
    from ptui.app import PtuiApp

    apps = []
    monkeypatch.setattr(PtuiApp, "run", lambda self, **kwargs: apps.append((self, kwargs)))
    return apps


def ptui(papis_lib, *args, config: str = "", keys: str = ""):
    """Run `ptui ARGS` with these files in the user's config dir.

    `papis_lib` has already pointed that into tmp_path (`$PAPIS_CONFIG_DIR/ptui`).

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture.
        config: The text of `config.toml`; empty writes nothing.
        keys: The text of `keys.toml`; empty writes nothing.
        *args: The command line.

    Returns:
        click's `Result`.
    """
    from ptui import cli

    folder = papis_lib / "config" / "ptui"
    folder.mkdir(exist_ok=True)
    for name, text in (("config.toml", config), ("keys.toml", keys)):
        if text:
            (folder / name).write_text(text)
    return CliRunner().invoke(cli.main, list(args))


# Off: the state file's default is the developer's real ~/.local/state.
QUIET = '[general]\npersist_state = false\n[log]\nfile = ""\n'


def test_a_clean_start_hands_the_library_and_the_query_to_the_app(papis_lib, started):
    """A clean start hands the library and the query to the app.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        started: The `started` fixture: `(app, run's keyword arguments)` per app started.
    """
    result = ptui(papis_lib, "-l", "test", "year:2016", "tags:test", config=QUIET + "[ui]\nmouse = false\n")
    assert result.exit_code == 0, result.output
    [(app, kwargs)] = started
    assert app.config.get("general.library") == "test"
    assert app.scope_query == "year:2016 tags:test"  # the words, joined back into one query
    assert kwargs == {"mouse": False}


def test_a_broken_keys_toml_is_one_line_naming_it(papis_lib, started):
    """A broken keys toml is one line naming it.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        started: The `started` fixture: `(app, run's keyword arguments)` per app started.
    """
    result = ptui(papis_lib, config=QUIET, keys='[modes.list\n"j" = { cmd = "nav.down" }\n')
    assert result.exit_code == 1
    [line] = result.output.splitlines()
    assert line.startswith("ptui: ") and "keys.toml" in line
    assert "Traceback" not in result.output
    assert started == []


def test_a_binding_with_no_command_names_the_key(papis_lib, started):
    """A binding with no command names the key.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        started: The `started` fixture: `(app, run's keyword arguments)` per app started.
    """
    result = ptui(papis_lib, config=QUIET, keys='[modes.list]\n"j" = { desc = "down, I hope" }\n')
    assert result.exit_code == 1
    [line] = result.output.splitlines()
    assert "keys.toml" in line and "'j' needs a cmd" in line
    assert started == []


def test_a_prefix_conflict_refuses_to_start_and_names_both_chords(papis_lib, started):
    """A prefix conflict refuses to start and names both chords.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        started: The `started` fixture: `(app, run's keyword arguments)` per app started.
    """
    # both in the user's own table: a conflict whatever the merge with ours does
    keys = '[modes.list]\n"x" = { cmd = "nav.down" }\n"x y" = { cmd = "nav.up" }\n'
    result = ptui(papis_lib, config=QUIET, keys=keys)
    assert result.exit_code == 1
    assert "prefix conflicts" in result.output
    assert "'x' shadows 'x y'" in result.output
    assert started == []


def test_an_unknown_command_is_not_fatal(papis_lib, started):
    """Collected and logged at startup, not refused: the keymap can bind ahead.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        started: The `started` fixture: `(app, run's keyword arguments)` per app started.
    """
    result = ptui(papis_lib, config=QUIET, keys='[modes.list]\n"j" = { cmd = "nav.sideways" }\n')
    assert result.exit_code == 0, result.output
    [(app, _)] = started
    assert any("nav.sideways" in problem for problem in app.keymap.unknown_commands)


def test_a_broken_config_toml_beats_a_broken_keys_toml(papis_lib, started):
    """Both broken: one line, for the file read first, and nothing started.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
        started: The `started` fixture: `(app, run's keyword arguments)` per app started.
    """
    result = ptui(papis_lib, config="[ui\n", keys="[modes.list\n")
    assert result.exit_code == 1
    [line] = result.output.splitlines()
    assert "config.toml" in line and "keys.toml" not in line
    assert started == []
