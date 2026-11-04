"""Edges of the `c` verbs and `files.relocate`.

Tags stored as text, undo of a batch, a bad value in a batch, clamped ratings, and a relocate that
fails halfway through one document.
"""

import pytest
from conftest import press, settle

from ptui import actions, place, ui


def _info(papis_lib, doc):
    """Find a document's `info.yaml`.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture.
        doc: The document's folder name.

    Returns:
        The file's path.
    """
    return papis_lib / "lib" / doc / "info.yaml"


def _retag(papis_lib, doc, line):
    """Replace a document's `tags` line on disk.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture.
        doc: The document's folder name.
        line: The new line.
    """
    info = _info(papis_lib, doc)
    info.write_text(info.read_text().replace("tags: [test]", line))


def _doc(app, papis_id):
    """Find a document of the scoped set.

    Args:
        app: The running `PtuiApp`.
        papis_id: The document's id.

    Returns:
        The document.
    """
    return next(doc for doc in app.docs if doc["papis_id"] == papis_id)


async def test_tagging_a_document_whose_tags_are_text_writes_a_list(app, papis_lib):
    """Tagging a document whose tags are text writes a list.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    _retag(papis_lib, "doc0", "tags: ml, cv")  # legal YAML, the wrong type
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_tag(app, "nlp")
        await settle(pilot)
        assert app.current["tags"] == ["ml", "cv", "nlp"]


async def test_undoing_a_batch_tag_gives_each_document_its_own_tags_back(app, papis_lib):
    """Undoing a batch tag gives each document its own tags back.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    _retag(papis_lib, "doc1", "tags: [vision]")
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        actions.doc_tag(app, "shared")
        assert isinstance(app.screen, ui.SelectList)
        await press(pilot, "enter")
        assert [_doc(app, papis_id)["tags"] for papis_id in ("id0", "id1")] == [
            ["test", "shared"],
            ["vision", "shared"],
        ]

        await press(pilot, "u")
        assert [_doc(app, papis_id)["tags"] for papis_id in ("id0", "id1")] == [["test"], ["vision"]]
        assert _doc(app, "id2")["tags"] == ["test"]  # never a target

        await press(pilot, "ctrl+r")
        assert [_doc(app, papis_id)["tags"] for papis_id in ("id0", "id1")] == [
            ["test", "shared"],
            ["vision", "shared"],
        ]


async def test_a_bad_value_aborts_a_batch_before_anything_is_asked_or_written(app, papis_lib):
    """A bad value aborts a batch before anything is asked or written.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    before = [_info(papis_lib, folder).read_text() for folder in ("doc0", "doc1")]
    async with app.run_test() as pilot:
        await settle(pilot)
        app.marks = {"id0", "id1"}
        actions.doc_set(app, "year", "twenty")
        await settle(pilot)
        assert not isinstance(app.screen, ui.SelectList)
        assert not app.history.done
    assert [_info(papis_lib, folder).read_text() for folder in ("doc0", "doc1")] == before


@pytest.mark.parametrize(("value", "stored"), [(9, 5), (-3, None)])
async def test_a_rating_outside_zero_to_five_is_clamped(app, value, stored):
    """A rating outside zero to five is clamped.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        value: Parametrized, such as `9`.
        stored: Parametrized, such as `5`.
    """
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_rating(app, value)
        await settle(pilot)
        assert app.current.get("rating") == stored


async def test_untagging_a_document_without_tags_adds_no_key(app, papis_lib):
    """Untagging a document without tags adds no key.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    info = _info(papis_lib, "doc0")
    info.write_text(info.read_text().replace("tags: [test]\n", ""))
    async with app.run_test() as pilot:
        await settle(pilot)
        actions.doc_untag(app, "test")
        await settle(pilot)
        assert "tags" not in app.current
    assert "tags" not in info.read_text()


async def test_a_relocate_that_fails_on_one_entry_leaves_no_dangling_entry(app, papis_lib):
    """A relocate that fails on one entry leaves no dangling entry.

    Args:
        app: The `PtuiApp` fixture, not started yet.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    folder = papis_lib / "lib" / "doc0"
    (folder / "a.pdf").write_bytes(b"pdf")
    (folder / "b.djvu").write_bytes(b"djvu")
    info = _info(papis_lib, "doc0")
    info.write_text(info.read_text() + "files:\n- a.pdf\n- b.djvu\n")
    pdfs = papis_lib / "pdfs"
    pdfs.mkdir()
    (pdfs / "2017.djvu").symlink_to(papis_lib / "moved-away.djvu")

    async with app.run_test() as pilot:
        await settle(pilot)
        app.config.data["files"]["rules"] = [
            {"match": [f"*.{ext}"], "op": "move", "dest": f"{{pdf_root}}/{{doc[year]}}.{ext}"}
            for ext in ("pdf", "djvu")
        ]
        app.run_command("files.relocate")
        await settle(pilot)
        actions.reload(app, rescan=True)
        await settle(pilot)
        doc = _doc(app, "id0")
        assert all(place.resolve(doc, entry).exists() for entry in doc["files"]), doc["files"]
