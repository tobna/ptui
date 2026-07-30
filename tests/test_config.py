from ptui import config


def test_defaults_load():
    cfg = config.load(config.DEFAULTS_DIR / "nonexistent.toml")
    assert cfg.get("ui.layout") == "auto"
    assert cfg.get("list.sort_presets")[0]["key"] == "time-added"
    assert cfg.unknown == ()


def test_user_overrides_merge_per_key(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[ui]\ntheme = "mine"\n\n[nope]\nx = 1\n\n[ui.also]\ny = 2\n'
    )
    cfg = config.load(tmp_path / "config.toml")
    assert cfg.get("ui.theme") == "mine"
    assert cfg.get("ui.layout") == "auto"  # untouched sibling survives
    assert set(cfg.unknown) == {"nope", "ui.also"}


def test_lists_are_replaced_not_merged(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[[list.columns]]\ntitle = "Only"\nformat = "{doc[ref]}"\n'
    )
    cfg = config.load(tmp_path / "config.toml")
    assert [c["title"] for c in cfg.get("list.columns")] == ["Only"]


def test_as_path_expands(tmp_path):
    (tmp_path / "config.toml").write_text('[files]\npdf_root = "~/x"\n')
    cfg = config.load(tmp_path / "config.toml")
    assert cfg.as_path("files.pdf_root").is_absolute()
    assert cfg.as_path("export.bib_target") is None
