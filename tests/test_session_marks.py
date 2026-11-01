"""Sessions that mark a few documents and act on them, then take it back."""

import pytest
import yaml
from conftest import press, settle
from session_lib import awkward_app, hints, info_text, row_of, screen, status


def on_disk(papis_lib, name: str) -> dict:
    return yaml.safe_load((papis_lib / "lib" / name / "info.yaml").read_text())


@pytest.mark.parametrize("size", [(80, 24), (200, 50)])
async def test_narrow_mark_two_tag_them_and_undo(papis_lib, size):
    """`/nlp`, mark two of the three, `c t survey`, read the files, `u`."""
    from ptui import ui

    app = awkward_app(papis_lib)
    vaswani = (papis_lib / "lib" / "vaswani" / "info.yaml").read_text()
    async with app.run_test(size=size) as pilot:
        await settle(pilot)
        await press(pilot, "/", *"nlp", "enter")
        for title in ("Attention Is All", "BERT", "Neural Probabilistic"):
            row_of(app, title)  # on screen, once
        assert "Scaling Laws" not in screen(app)  # narrowed away
        assert "/ nlp" in status(app)
        assert "3 shown / 10 total" in status(app)

        await press(pilot, "space", "space")  # marks and advances: Vaswani, Devlin
        mark = ui.glyph("mark")
        assert mark in row_of(app, "Attention Is All")
        assert mark in row_of(app, "BERT")
        assert mark not in row_of(app, "Neural Probabilistic")
        assert "2 marked (2 visible) 3 shown / 10 total" in status(app)
        assert "c t add tags" in hints(app)  # the hint bar turns to batch verbs

        await press(pilot, "c", "t", *"survey", "enter")
        await press(pilot, "enter")  # two documents: the batch asks first
        assert on_disk(papis_lib, "vaswani")["tags"] == ["transformers", "nlp", "survey"]
        assert on_disk(papis_lib, "devlin")["tags"] == ["nlp", "transformers", "survey"]
        assert on_disk(papis_lib, "bengio")["tags"] == ["nlp", "classic"]  # on screen, but not marked
        text = (papis_lib / "lib" / "vaswani" / "info.yaml").read_text()
        assert "# added by hand; keep this comment" in text  # the safe write keeps the comment
        assert "x-reviewer: Reviewer Two" in text  # and the key papis does not know
        await press(pilot, "g", "g")
        assert "transformers · nlp · survey" in info_text(app)

        await press(pilot, "u")
        assert on_disk(papis_lib, "vaswani")["tags"] == ["transformers", "nlp"]
        assert on_disk(papis_lib, "devlin")["tags"] == ["nlp", "transformers"]
        assert "# added by hand; keep this comment" in (papis_lib / "lib" / "vaswani" / "info.yaml").read_text()
        assert "survey" not in screen(app)
        assert "transformers · nlp" in info_text(app)

        await press(pilot, "escape", "escape")  # the prompt is long gone: the first escape drops the narrow
        assert "10 shown / 10 total" in status(app)
        assert "2 marked (2 visible)" in status(app)  # escape never clears marks
        await press(pilot, "m", "c")
        assert "marked" not in status(app)
        assert mark not in screen(app)
        await press(pilot, "q")
    assert app.return_code == 0
    assert on_disk(papis_lib, "vaswani") == yaml.safe_load(vaswani)


async def test_visual_range_gets_a_reading_status_and_gives_it_back(papis_lib):
    """`j v j j`, `c s read`: three documents read, the rest untouched; `u`."""
    from ptui import ui

    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "j", "v")
        assert " VISUAL " in status(app)
        await press(pilot, "j", "j")
        mark = ui.glyph("mark")
        for title in ("深層学習", "BERT", "Scaling Laws"):
            assert mark in row_of(app, title)  # drawn as it is marked, live
        for title in ("Attention Is All", "Sparse Coding"):
            assert mark not in row_of(app, title)
        assert "3 marked (3 visible)" in status(app)

        await press(pilot, "c", "s")
        assert [item.label for item in app.screen.items][:3] == ["unread", "reading", "read"]
        await press(pilot, "down", "down", "enter")
        await press(pilot, "enter")  # three documents: confirm
        for name in ("yamada", "devlin", "kaplan"):
            assert on_disk(papis_lib, name)["reading_status"] == "read"
        for name in ("vaswani", "olshausen", "he"):
            assert "reading_status" not in on_disk(papis_lib, name)
        assert " VISUAL " not in status(app)  # the verb ended the range, the marks stay
        assert "3 marked" in status(app)
        assert "reading_status" in info_text(app)

        await press(pilot, "u")
        for name in ("yamada", "devlin", "kaplan"):
            assert "reading_status" not in on_disk(papis_lib, name)  # undo restores the absence
        assert "reading_status" not in info_text(app)


async def test_visual_unmark_takes_a_range_back_out_of_mark_all(papis_lib):
    """`m a` marks all ten, `G V k k` unmarks the last three, `g m` shows the rest."""
    from ptui import ui

    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "m", "a")
        assert "10 marked (10 visible)" in status(app)
        await press(pilot, "G", "V", "k", "k")
        assert " UNMARK " in status(app)
        assert "7 marked (7 visible)" in status(app)
        await press(pilot, "V")  # again ends it, keeping what it did
        assert " UNMARK " not in status(app)
        await press(pilot, "g", "m")  # marked only
        assert "7 marked (7 visible) 7 shown / 10 total" in status(app)
        shown = screen(app)
        assert "Denoising Diffusion" not in shown and "Neural Probabilistic" not in shown
        assert "Attention Is All" in shown
        assert ui.glyph("mark") in row_of(app, "Deep Residual")
