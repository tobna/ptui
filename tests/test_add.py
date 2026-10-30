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


async def test_add_from_a_bib_entry_creates_a_document_with_no_file(app, tmp_path):
    """The metadata-only path: an importer found a record but there is no PDF."""
    from conftest import press

    from ptui import actions, library, ui

    bib = tmp_path / "refs.bib"
    bib.write_text(
        "@inproceedings{he2016deep, title={Deep Residual Learning v2}, "
        "author={He, Kaiming and Zhang, Xiangyu}, year={2016}, booktitle={CVPR}}\n"
    )
    async with app.run_test() as pilot:
        await press(pilot, "escape")
        actions.prompt_result(app, "import:bib", str(bib))
        await pilot.pause(0.3)
        assert isinstance(app.screen, ui.AddForm)
        assert app.screen.source is None  # nothing to attach
        assert app.screen.query_one("#add-title").value == "Deep Residual Learning v2"
        assert app.screen.query_one("#add-year").value == "2016"
        assert "no file" in str(app.screen.query_one("#add-preview").content)

        await press(pilot, "enter")
        await pilot.pause(0.6)
        added = next((d for d in app.docs if d["title"] == "Deep Residual Learning v2"), None)
        assert added is not None
        assert list(added.get("files") or []) == []
        # a key the form never showed still has to survive the round trip
        assert added["booktitle"] == "CVPR"
        # papis's `add.run()` writes no `time-added`, so ptui stamps it — without which a
        # new document sorts to the *bottom* of "recently added"
        assert added[library.TIME_ADDED] > "2024-01-01-00:00:00"
        assert app.docs[0] is added


async def test_a_bib_with_several_entries_asks_which_one(app, tmp_path):
    from ptui import actions, ui

    bib = tmp_path / "many.bib"
    bib.write_text(
        "@article{a1, title={First One}, author={A, B}, year={2020}, journal={J}}\n"
        "@article{a2, title={Second One}, author={C, D}, year={2021}, journal={J}}\n"
    )
    async with app.run_test() as pilot:
        actions.prompt_result(app, "import:bib", str(bib))
        await pilot.pause(0.3)
        assert isinstance(app.screen, ui.SelectList)
        assert [item.label for item in app.screen.items] == ["First One", "Second One"]


async def test_switching_library_sends_a_new_document_to_that_library(app, papis_lib):
    """`papis add` writes into papis's *current* library, so switching only ptui's
    copy of the name filed the new document in the old one."""
    import papis.config

    from ptui import actions

    other = papis_lib / "other"
    other.mkdir()
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        app.run_command("lib.switch", {"name": str(other)})
        assert app.docs == []
        actions._add_document(app, None, {"title": "Lands Here"})
        await pilot.pause(0.3)
        assert papis.config.get_lib().path == str(other)
        assert [d["title"] for d in app.docs] == ["Lands Here"]
        assert any(other.glob("*/info.yaml"))
        assert len(list((papis_lib / "lib").glob("*/info.yaml"))) == 3  # the old one is untouched


async def test_moving_documents_to_another_library_skips_a_clash_and_moves_the_rest(app, papis_lib):
    import papis.database

    from ptui import library

    other = papis_lib / "other"
    (other / "doc0").mkdir(parents=True)  # taken in the target: a clash, never an overwrite
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {library.doc_id(d) for d in app.docs if d["papis_id"] in ("id0", "id1")}
        app.run_command("doc.move", {"to": str(other)})
        await press(pilot, "enter")  # a batch confirms first
        await settle(pilot)
        assert sorted(d["papis_id"] for d in app.docs) == ["id0", "id2"]
        moved = papis.database.get(str(other)).get_all_documents()
        assert [d["papis_id"] for d in moved] == ["id1"]
        assert (other / "doc1" / "info.yaml").exists()
        assert app.marks == {"id0"}  # the one that stayed is still marked


async def test_doc_add_with_a_uri_skips_both_prompts(app, monkeypatch):
    """`:doc.add arxiv 2607.00687` goes straight to the form, prefilled."""
    from ptui import actions, fetch, ui

    asked = []

    def fake(name, uri):
        asked.append((name, uri))
        return {"title": "Fetched Title", "year": 2026, "eprint": "2607.00687"}, []

    monkeypatch.setattr(fetch, "fetch", fake)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        actions.cmdline_run(app, "doc.add", "arxiv 2607.00687")
        await app.workers.wait_for_complete()
        await pilot.pause(0.2)
        assert asked == [("arxiv", "2607.00687")]
        assert isinstance(app.screen, ui.AddForm)
        assert app.screen.query_one("#add-title").value == "Fetched Title"
