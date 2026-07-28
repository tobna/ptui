from conftest import press, settle
from textual.widgets import Input, Static

from ptui import ui


async def test_add_from_inbox_previews_then_writes(app, papis_lib):
    inbox = papis_lib / "inbox"
    inbox.mkdir()
    (inbox / "some paper.pdf").write_bytes(b"pdf")
    app.cfg.data["files"]["inbox"] = str(inbox)

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "i")  # doc.add source=inbox
        assert isinstance(app.screen, ui.SelectList)
        await press(pilot, "enter")

        form = app.screen
        assert isinstance(form, ui.AddForm)
        preview = form.query_one("#add-preview", Static)
        assert "not resolvable yet" in str(preview.render())  # no author or year yet

        form.query_one("#add-title", Input).value = "Brand New Paper"
        form.query_one("#add-year", Input).value = "2020"
        form.query_one("#add-author", Input).value = "Bloggs, Jo"
        await pilot.pause()
        assert "2020_Bloggs_Brand_New_Paper.pdf" in str(preview.render())

        await press(pilot, "enter")
        await settle(pilot)

    assert [d["title"] for d in app.docs if d["title"] == "Brand New Paper"]
    assert (inbox / "some paper.pdf").exists()  # the source is never deleted
    placed = list((papis_lib / "pdfs").glob("2020_*.pdf"))
    assert len(placed) == 1  # the add flow ends in place(), like files.relocate


async def test_add_form_escape_writes_nothing(app, papis_lib):
    inbox = papis_lib / "inbox"
    inbox.mkdir()
    (inbox / "paper.pdf").write_bytes(b"pdf")
    app.cfg.data["files"]["inbox"] = str(inbox)

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "i")
        await press(pilot, "enter")
        await press(pilot, "escape")
        await settle(pilot)
        assert len(app.docs) == 3
    assert not (papis_lib / "pdfs").exists()
