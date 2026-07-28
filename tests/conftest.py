import pytest


@pytest.fixture(autouse=True)
def papis_lib(tmp_path, monkeypatch):
    """A throwaway papis library with three documents, isolated from the user's.

    Autouse: papis resolves the current library lazily from global state, so any
    test that touches `papis.format` would otherwise depend on whoever is
    running it having a library called `papers`.
    """
    import papis.config
    import papis.database

    previous = (papis.config.CURRENT_CONFIGURATION, papis.config.CURRENT_LIBRARY)

    docs = tmp_path / "lib"
    config_dir = tmp_path / "config"
    docs.mkdir()
    config_dir.mkdir()

    for i, (title, year, author) in enumerate(
        [
            ("Attention Is All You Need", 2017, "Vaswani"),
            ("Deep Residual Learning", 2016, "He"),
            ("A Neural Probabilistic Language Model", 2003, "Bengio"),
        ]
    ):
        folder = docs / f"doc{i}"
        folder.mkdir()
        (folder / "info.yaml").write_text(
            f"title: {title}\nyear: {year}\n"
            f"author: {author}\nauthor_list:\n- family: {author}\n  given: X\n"
            f"ref: {author}{year}\ntags: [test]\npapis_id: id{i}\n"
            f"time-added: 202{4 - i}-01-01-00:00:00\n"  # newest added first
        )

    (config_dir / "config").write_text(
        f"[settings]\ndatabase-backend = papis\ndefault-library = test\n\n[test]\ndir = {docs}\n"
    )
    monkeypatch.setenv("PAPIS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PAPIS_LIB", "test")
    papis.config.CURRENT_CONFIGURATION = None
    papis.config.CURRENT_LIBRARY = None
    papis.database.clear_cached()
    yield tmp_path
    papis.database.clear_cached()
    papis.config.CURRENT_CONFIGURATION, papis.config.CURRENT_LIBRARY = previous
