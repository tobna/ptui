"""Config loading, merging and repair."""

from ptui import config


def test_defaults_load():
    """The shipped defaults load when the user has no config file."""
    configuration = config.load(config.DEFAULTS_DIR / "nonexistent.toml")
    assert configuration.get("ui.layout") == "auto"
    assert configuration.get("list.sort_presets")[0]["key"] == "time-added"
    assert configuration.unknown == ()


def test_session_restore_is_opt_in():
    """A plain start opens the library as it did before restore existed."""
    assert config.load(config.DEFAULTS_DIR / "nonexistent.toml").get("general.persist_state") is False


def test_user_overrides_merge_per_key(tmp_path):
    """User overrides merge per key.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "config.toml").write_text('[ui]\ntheme = "mine"\n\n[nope]\nx = 1\n\n[ui.also]\ny = 2\n')
    configuration = config.load(tmp_path / "config.toml")
    assert configuration.get("ui.theme") == "mine"
    assert configuration.get("ui.layout") == "auto"  # untouched sibling survives
    assert set(configuration.unknown) == {"nope", "ui.also"}


def test_lists_are_replaced_not_merged(tmp_path):
    """Lists are replaced not merged.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "config.toml").write_text('[[list.columns]]\ntitle = "Only"\nformat = "{doc[ref]}"\n')
    configuration = config.load(tmp_path / "config.toml")
    assert [column["title"] for column in configuration.get("list.columns")] == ["Only"]


def test_a_choice_in_the_wrong_case_is_folded_not_misread(tmp_path):
    """A choice in the wrong case is folded not misread.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "config.toml").write_text('[ui]\nlayout = "Horizontal"\n[undo]\nstrategy = "GIT"\n')
    configuration = config.load(tmp_path / "config.toml")
    assert configuration.get("ui.layout") == "horizontal"
    assert configuration.get("undo.strategy") == "git"
    assert configuration.invalid == ()


def test_as_path_expands(tmp_path):
    """`Config.as_path` expands `~`, and an unset key gives `None`.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "config.toml").write_text('[files]\npdf_root = "~/x"\n')
    configuration = config.load(tmp_path / "config.toml")
    assert configuration.as_path("files.pdf_root").is_absolute()
    assert configuration.as_path("export.bib_target") is None


def test_a_broken_config_names_the_file_and_the_cli_refuses_politely(tmp_path, monkeypatch):
    """A broken config names the file and the cli refuses politely.

    A typo in config.toml or keys.toml was a Python traceback that never said
    which file it came from.

    Args:
        tmp_path: pytest's per-test temporary folder.
        monkeypatch: pytest's monkeypatch fixture.
    """
    import pytest
    from click.testing import CliRunner

    from ptui import cli, keymap

    bad = tmp_path / "config.toml"
    bad.write_text("[ui\nicons = true\n")
    with pytest.raises(ValueError, match=r"config\.toml"):
        config.load(bad)

    keys = tmp_path / "keys.toml"
    keys.write_text('[modes.list]\n"j" = { desc = "no command" }\n')
    with pytest.raises(ValueError, match=r"keys.toml: \[modes.list\] 'j' needs a cmd"):
        keymap.load(keys)
    keys.write_text('[modes.list]\n"j" = "nav.down"\n')
    with pytest.raises(ValueError, match="needs a cmd"):
        keymap.load(keys)

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    result = CliRunner().invoke(cli.main, [])
    assert result.exit_code == 1
    assert "config.toml" in result.output
    assert "Traceback" not in result.output


def test_a_value_that_cannot_work_falls_back_to_the_default_and_says_so(tmp_path):
    """A value that cannot work falls back to the default and says so.

    `log.level = "loud"` and a column with no width crashed at startup;
    `layout = "diagonal"` and `split_ratio = 1.5` quietly broke the layout.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "config.toml").write_text(
        '[ui]\nlayout = "diagonal"\nsplit_ratio = 1.5\n[log]\nlevel = "loud"\n[list]\nrow_height = 0\n'
        '[[list.columns]]\ntitle = "Title"\nformat = "{doc[title]}"\n'
        '[[list.columns]]\ntitle = "Broken"\n'
    )
    configuration = config.load(tmp_path / "config.toml")
    assert configuration.get("ui.layout") == "auto"
    assert configuration.get("ui.split_ratio") == 0.6
    assert configuration.get("log.level") == "info"
    assert configuration.get("list.row_height") == 1
    assert configuration.get("list.columns") == [{"title": "Title", "format": "{doc[title]}", "width": 0}]
    assert len(configuration.invalid) == 5
    assert any("ui.layout = 'diagonal'" in problem for problem in configuration.invalid)


def test_a_users_source_joins_the_shipped_ones_and_a_broken_one_is_left_out(tmp_path):
    """A users source joins the shipped ones and a broken one is left out.

    `[add.sources]` merges per key: the shipped rows keep their order, a
    shipped name changes only what it sets, and a row missing a field is
    reported rather than crashing the `a` picker.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "config.toml").write_text(
        "[add.sources]\n"
        'arxiv = { label = "arXiv" }\n'
        'mine = { label = "my plugin", example = "a plugin id" }\n'
        'half = { label = "no example" }\n'
        'flat = "just text"\n'
        "doi = { label = 3 }\n"
        '[query.aliases]\nzz = "tags:"\n'
    )
    configuration = config.load(tmp_path / "config.toml")
    sources = configuration.get("add.sources")
    assert list(sources)[:2] == ["arxiv", "doi"]
    assert list(sources)[-1] == "mine"
    assert sources["arxiv"]["label"] == "arXiv"
    assert sources["arxiv"]["example"].startswith("2509.26092")
    assert sources["mine"] == {"label": "my plugin", "example": "a plugin id"}
    assert "half" not in sources and "flat" not in sources
    assert sources["doi"]["label"] == "a DOI"  # a broken shipped row falls back to itself
    assert sorted(problem.split(" = ")[0] for problem in configuration.invalid) == [
        "add.sources.doi",
        "add.sources.flat",
        "add.sources.half",
    ]
    assert configuration.unknown == ()  # keys the user names are not typos


def test_an_empty_column_list_gets_the_shipped_columns(tmp_path):
    """An empty column list gets the shipped columns.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "config.toml").write_text("[list]\ncolumns = []\n")
    configuration = config.load(tmp_path / "config.toml")
    assert [column["title"] for column in configuration.get("list.columns")][-2:] == ["Title", "Tags"]


def test_the_shipped_config_is_valid(tmp_path):
    """The shipped config is valid.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    (tmp_path / "config.toml").write_text("")
    assert config.load(tmp_path / "config.toml").invalid == ()


async def test_edit_editor_overrides_papis_editor(papis_lib):
    """`[edit] editor` was shipped and documented, and read by nothing.

    `E` ran papis's editor whatever it said.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    import papis.config
    from conftest import build_app

    app = build_app(papis_lib, '[edit]\neditor = "my-editor --wait"\n')
    async with app.run_test():
        assert papis.config.getstring("editor") == "my-editor --wait"


def test_every_shipped_key_is_read_somewhere():
    """A key in the shipped config that no code reads is a promise nothing keeps."""
    import tomllib

    source_text = "\n".join(path.read_text() for path in (config.DEFAULTS_DIR.parent).glob("*.py"))
    data = tomllib.loads((config.DEFAULTS_DIR / "config.toml").read_text())

    def leaves(node, prefix=""):
        """Walk a table down to its settings.

        Args:
            node: The table.
            prefix: Dotted path of the table, ending in a dot.

        Yields:
            Each setting's dotted key.
        """
        for key, value in node.items():
            # tables of user data, read whole
            if isinstance(value, dict) and key not in ("aliases", "saved", "kind_patterns", "default", "sources"):
                yield from leaves(value, f"{prefix}{key}.")
            else:
                yield f"{prefix}{key}"

    unread = [key for key in leaves(data) if f'"{key}' not in source_text]
    assert unread == []
