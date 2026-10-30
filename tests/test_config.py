from ptui import config


def test_defaults_load():
    cfg = config.load(config.DEFAULTS_DIR / "nonexistent.toml")
    assert cfg.get("ui.layout") == "auto"
    assert cfg.get("list.sort_presets")[0]["key"] == "time-added"
    assert cfg.unknown == ()


def test_user_overrides_merge_per_key(tmp_path):
    (tmp_path / "config.toml").write_text('[ui]\ntheme = "mine"\n\n[nope]\nx = 1\n\n[ui.also]\ny = 2\n')
    cfg = config.load(tmp_path / "config.toml")
    assert cfg.get("ui.theme") == "mine"
    assert cfg.get("ui.layout") == "auto"  # untouched sibling survives
    assert set(cfg.unknown) == {"nope", "ui.also"}


def test_lists_are_replaced_not_merged(tmp_path):
    (tmp_path / "config.toml").write_text('[[list.columns]]\ntitle = "Only"\nformat = "{doc[ref]}"\n')
    cfg = config.load(tmp_path / "config.toml")
    assert [c["title"] for c in cfg.get("list.columns")] == ["Only"]


def test_as_path_expands(tmp_path):
    (tmp_path / "config.toml").write_text('[files]\npdf_root = "~/x"\n')
    cfg = config.load(tmp_path / "config.toml")
    assert cfg.as_path("files.pdf_root").is_absolute()
    assert cfg.as_path("export.bib_target") is None


def test_a_broken_config_names_the_file_and_the_cli_refuses_politely(tmp_path, monkeypatch):
    """A typo in config.toml or keys.toml was a Python traceback that never said
    which file it came from."""
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
    """`log.level = "loud"` and a column with no width crashed at startup;
    `layout = "diagonal"` and `split_ratio = 1.5` quietly broke the layout."""
    (tmp_path / "config.toml").write_text(
        '[ui]\nlayout = "diagonal"\nsplit_ratio = 1.5\n[log]\nlevel = "loud"\n[list]\nrow_height = 0\n'
        '[[list.columns]]\ntitle = "Title"\nformat = "{doc[title]}"\n'
        '[[list.columns]]\ntitle = "Broken"\n'
    )
    cfg = config.load(tmp_path / "config.toml")
    assert cfg.get("ui.layout") == "auto"
    assert cfg.get("ui.split_ratio") == 0.6
    assert cfg.get("log.level") == "info"
    assert cfg.get("list.row_height") == 1
    assert cfg.get("list.columns") == [{"title": "Title", "format": "{doc[title]}", "width": 0}]
    assert len(cfg.invalid) == 5
    assert any("ui.layout = 'diagonal'" in problem for problem in cfg.invalid)


def test_an_empty_column_list_gets_the_shipped_columns(tmp_path):
    (tmp_path / "config.toml").write_text("[list]\ncolumns = []\n")
    cfg = config.load(tmp_path / "config.toml")
    assert [c["title"] for c in cfg.get("list.columns")][-2:] == ["Title", "Tags"]


def test_the_shipped_config_is_valid(tmp_path):
    (tmp_path / "config.toml").write_text("")
    assert config.load(tmp_path / "config.toml").invalid == ()


async def test_edit_editor_overrides_papis_editor(papis_lib):
    """`[edit] editor` was shipped and documented, and read by nothing: `E` ran
    papis's editor whatever it said."""
    import papis.config
    from conftest import build_app

    app = build_app(papis_lib, '[edit]\neditor = "my-editor --wait"\n')
    async with app.run_test():
        assert papis.config.getstring("editor") == "my-editor --wait"


def test_every_shipped_key_is_read_somewhere():
    """A key in the shipped config that no code reads is a promise nothing keeps."""
    import tomllib

    src = "\n".join(path.read_text() for path in (config.DEFAULTS_DIR.parent).glob("*.py"))
    data = tomllib.loads((config.DEFAULTS_DIR / "config.toml").read_text())

    def leaves(node, prefix=""):
        for key, value in node.items():
            # tables of user data, read whole
            if isinstance(value, dict) and key not in ("aliases", "saved", "kind_patterns", "default"):
                yield from leaves(value, f"{prefix}{key}.")
            else:
                yield f"{prefix}{key}"

    unread = [key for key in leaves(data) if f'"{key}' not in src]
    assert unread == [
        "general.persist_state",
        "query.history_file",
        "edit.structured_fields",
    ]  # the backlog's own entries, each shrinking this list as it lands
