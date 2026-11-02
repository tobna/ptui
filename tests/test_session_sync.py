"""Sessions where the library changes on disk under a running ptui — a sync, a
`git pull`, a script — and the person then goes on working in it."""

import shutil

import yaml
from conftest import press, settle
from session_lib import awkward_app, info_text, list_lines, status


def edit_outside(papis_lib, name: str, old: str, new: str) -> None:
    """What a script or a sync does: write `info.yaml` behind papis's back."""
    info = papis_lib / "lib" / name / "info.yaml"
    info.write_text(info.read_text().replace(old, new))


async def test_a_rescan_picks_up_documents_a_sync_added_and_removed(papis_lib):
    app = awkward_app(papis_lib)
    lib = papis_lib / "lib"
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        (lib / "newcomer").mkdir()
        (lib / "newcomer" / "info.yaml").write_text(
            "title: A Synced Newcomer\nyear: 2025\nref: new2025\npapis_id: s-new\n"
        )
        shutil.rmtree(lib / "bengio")

        await press(pilot, ":", *"app.reload", "enter", *"true", "enter")
        shown = "\n".join(list_lines(app))
        assert "A Synced Newcomer" in shown
        assert "Neural Probabilistic" not in shown
        assert "10 shown / 10 total" in status(app)
        await press(pilot, "g", "r", *"new2025", "enter")
        assert info_text(app).startswith("A Synced Newcomer")


async def test_a_tag_added_outside_survives_a_tag_added_inside(papis_lib):
    """A script tags the paper while ptui shows it; then the person tags it too.
    The tags used to be worked out from the copy loaded at startup."""
    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        edit_outside(papis_lib, "vaswani", "tags: [transformers, nlp]", "tags: [transformers, nlp, from-script]")
        await press(pilot, "c", "t", *"survey", "enter")
        tags = yaml.safe_load((papis_lib / "lib" / "vaswani" / "info.yaml").read_text())["tags"]
        # refusing ("changed on disk; press r") or merging would both keep it
        assert "from-script" in tags


async def test_after_a_rescan_the_list_and_the_info_pane_agree_on_an_edited_title(papis_lib):
    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        edit_outside(papis_lib, "vaswani", "title: Attention Is All You Need", "title: Attention Is Still Enough")
        # the `:` line's prompt takes a row, the list pane resizes and redraws:
        # that redraw is what caches the old title under the file's new mtime
        await press(pilot, ":", *"app.reload", "enter", *"true", "enter")
        assert info_text(app).startswith("Attention Is Still Enough")
        assert "Attention Is Still Enough" in list_lines(app)[2]
