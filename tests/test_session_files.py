"""Sessions in the files pane and with whole documents: open, detach, delete —
each checked on disk and taken back with `u`."""

import yaml
from conftest import press, settle
from session_lib import awkward_app, info_text, lines, list_lines, screen, status


def pane_lines(app, pane: str) -> list[str]:
    """The text inside one pane's border, cropped by cells."""
    region = app.query_one(f"#{pane}").region
    strips = app.screen._compositor.render_strips()[region.y + 1 : region.bottom - 1]
    return [strip.crop(region.x + 1, region.right - 1).text.strip() for strip in strips]


def files_on_disk(papis_lib, name: str) -> list[str]:
    return yaml.safe_load((papis_lib / "lib" / name / "info.yaml").read_text()).get("files", [])


async def test_detach_the_supplement_undo_and_quit_from_the_files_pane(papis_lib):
    from ptui import ui

    app = awkward_app(papis_lib)
    folder = papis_lib / "lib" / "he"
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "g", "r", *"he2016", "enter", "3")
        assert " FILES " in status(app)
        files = pane_lines(app, "files-pane")
        assert files[0] == "resnet.pdf"
        assert files[1].split() == ["resnet_supp.pdf", "supplement"]  # the kind is inferred, not stored
        assert ui.glyph("warning") not in "".join(files)

        await press(pilot, "j", "x")  # the supplement
        assert files_on_disk(papis_lib, "he") == ["resnet.pdf"]
        assert (folder / "resnet_supp.pdf").exists()  # detached, not deleted
        assert pane_lines(app, "files-pane")[0] == "resnet.pdf"
        assert "resnet_supp.pdf" not in screen(app)
        assert "files (1)" in info_text(app)

        await press(pilot, "u")
        assert files_on_disk(papis_lib, "he") == ["resnet.pdf", "resnet_supp.pdf"]
        assert "resnet_supp.pdf" in pane_lines(app, "files-pane")[1]
        assert "files (2)" in info_text(app)

        await press(pilot, "escape")  # transient: escape takes it off the screen
        assert not any("─ files ─" in line for line in lines(app))
        assert " LIST " in status(app)
        await press(pilot, "3", "q")
    assert app.return_code == 0


async def test_open_a_bracketed_file_and_a_missing_one(papis_lib, monkeypatch):
    import papis.utils

    from ptui import ui

    opened = []
    monkeypatch.setattr(papis.utils, "open_file", lambda path, **_: opened.append(path))
    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "g", "r", *"kaplan2020", "enter", "3")
        assert pane_lines(app, "files-pane")[0] == "scaling[v2].pdf"  # brackets as typed
        await press(pilot, "enter")
        assert opened == [str(papis_lib / "lib" / "kaplan" / "scaling[v2].pdf")]

        await press(pilot, "escape", "g", "r", *"lost2015", "enter", "3")
        files = pane_lines(app, "files-pane")
        assert files[0].split() == [ui.glyph("warning"), "gone.pdf"]  # flagged where you act on it
        await press(pilot, "enter", "4")
        assert opened == [str(papis_lib / "lib" / "kaplan" / "scaling[v2].pdf")]  # nothing handed off
        log = "".join(pane_lines(app, "log-pane"))  # a long path wraps, so read it unwrapped
        assert "missing file:" in log
        assert str(papis_lib / "lib" / "lost" / "gone.pdf") in log


async def test_delete_a_document_and_bring_it_back(papis_lib):
    """`d d enter` on the bracketed one, then `u`. Only what any version of the
    dialog must give: gone from screen and disk, then back, file and all."""
    app = awkward_app(papis_lib)
    folder = papis_lib / "lib" / "kaplan"
    before = (folder / "info.yaml").read_text()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "g", "r", *"kaplan2020", "enter")
        await press(pilot, "d", "d", "enter")
        assert not folder.exists()
        assert "Scaling Laws" not in "\n".join(list_lines(app))
        assert "9 shown / 9 total" in status(app)
        assert not info_text(app).startswith("Scaling Laws")

        await press(pilot, "u")
        assert (folder / "info.yaml").read_text() == before
        assert (folder / "scaling[v2].pdf").exists()
        assert "10 shown / 10 total" in status(app)
        assert "Scaling Laws [Extended Version]" in "\n".join(list_lines(app))
        await press(pilot, "q")
    assert app.return_code == 0
