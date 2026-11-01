"""Sessions that edit one document's fields and copy it out, then undo."""

import yaml
from conftest import press, settle
from session_lib import awkward_app, info_text, row_of


def on_disk(papis_lib, name: str) -> dict:
    return yaml.safe_load((papis_lib / "lib" / name / "info.yaml").read_text())


async def test_edit_a_bracketed_title_in_the_form_rate_it_and_undo_both(papis_lib):
    from ptui import ui

    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "g", "r", *"kaplan2020", "enter")
        await press(pilot, "E")  # the structured form, since `e` is $EDITOR by default
        await press(pilot, "end", *" (v2)", "enter")
        assert on_disk(papis_lib, "kaplan")["title"] == "Scaling Laws [Extended Version] (v2)"
        assert on_disk(papis_lib, "kaplan")["author"] == "Kaplan, Jared"  # untouched fields stay as they were
        assert "Scaling Laws [Extended Version] (v2)" in row_of(app, "Scaling Laws")
        assert info_text(app).startswith("Scaling Laws [Extended Version] (v2)")

        await press(pilot, "c", "r", "3", "enter")
        assert on_disk(papis_lib, "kaplan")["rating"] == 3
        stars = next(line for line in info_text(app).splitlines() if "rating" in line)
        assert stars.split()[-1] == ui.glyph("star") * 3 + ui.glyph("star_empty") * 2

        await press(pilot, "u")
        assert "rating" not in on_disk(papis_lib, "kaplan")
        await press(pilot, "u")
        assert on_disk(papis_lib, "kaplan")["title"] == "Scaling Laws [Extended Version]"
        assert info_text(app).startswith("Scaling Laws [Extended Version]\n")
        await press(pilot, "ctrl+r")  # and redo puts the edit back
        assert on_disk(papis_lib, "kaplan")["title"] == "Scaling Laws [Extended Version] (v2)"
        await press(pilot, "q")
    assert app.return_code == 0


async def test_yank_copies_the_stored_latex_not_the_screen(papis_lib, monkeypatch):
    """SPEC: titles render de-braced, but a yank or a bib export is verbatim."""
    app = awkward_app(papis_lib)
    copied = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)  # the fixture asks for OSC52
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "g", "r", *"devlin2019", "enter")
        assert info_text(app).startswith("BERT: Pre-training")

        await press(pilot, "y", "y")
        assert copied[-1] == r"\cite{devlin2019}"
        await press(pilot, "y", "b")
        assert "@inproceedings{devlin2019," in copied[-1]
        assert "{B}ERT: Pre-training" in copied[-1]

        await press(pilot, "j", "space", "space")  # Kaplan and Olshausen, marked
        await press(pilot, "y", "y")
        assert copied[-1] == r"\cite{kaplan2020} \cite{olshausen-draft}"
