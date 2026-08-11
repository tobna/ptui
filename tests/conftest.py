import pytest


async def press(pilot, *keys):
    for key in keys:
        await pilot.press(key)
    await settle(pilot)


async def settle(pilot):
    """Let the debounced narrow worker finish."""
    await pilot.pause(0.2)


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


@pytest.fixture
def app(papis_lib):
    """The app under test, wired to the throwaway library.

    Every path that leaves the library is redirected into `tmp_path`: `osc52`
    keeps the run out of the developer's real clipboard, and `undo.trash_dir`
    keeps it out of their real `~/.local/share/ptui/trash` — a merge moves folders
    there, and a test that used the shipped default silently filled it up.
    """
    from ptui import config, keymap
    from ptui.app import PtuiApp

    ptui_config = papis_lib / "ptui.toml"
    ptui_config.write_text(
        f'[export]\nclipboard = "osc52"\n\n'
        f'[files]\npdf_root = "{papis_lib / "pdfs"}"\n'
        f'[log]\nfile = ""\n'
        # No startup doctor scan: it is a thread nothing here asserts on, and it
        # competed with the 0.2s `settle` for the GIL — under load the first
        # press landed before the list had rows, and the command found no target.
        f"[doctor]\nscan_on_startup = false\n"
        f'[undo]\ntrash_dir = "{papis_lib / "trash"}"\n'
    )
    return PtuiApp(config.load(ptui_config), keymap.load(papis_lib / "no-keys.toml"))
