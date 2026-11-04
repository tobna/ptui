"""Sessions that move around: sorting, jumping, scopes, saved searches,
libraries and the log — reading the screen after every step."""

import json

from conftest import press, settle
from session_lib import awkward_app, info_text, lines, list_lines, row_of, screen, status


def titles(app) -> list[str]:
    """The list's rows top to bottom, by the first words of each title."""
    known = ("Attention", "深層学習", "BERT", "Scaling", "Sparse", "Benchmark", "Deep Residual", "A Paper", "Denoising")
    out = []
    for line in list_lines(app):
        out += [word for word in (*known, "A Neural") if word in line]
    return out


async def test_narrow_sort_reverse_and_jump_by_citekey(papis_lib):
    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "/", *"deep", "enter", "j")
        assert titles(app) == ["BERT", "Deep Residual"]
        assert info_text(app).startswith("Deep Residual")

        await press(pilot, "S", *"year", "enter")
        assert "/ deep" in status(app)  # sorting keeps the narrow...
        assert "Year ↓" in lines(app)[1]
        assert titles(app) == ["BERT", "Deep Residual"]
        assert info_text(app).startswith("Deep Residual")  # ...and the cursor's document

        await press(pilot, "ctrl+s")
        assert "Year ↑" in lines(app)[1]
        assert titles(app) == ["Deep Residual", "BERT"]
        assert info_text(app).startswith("Deep Residual")

        await press(pilot, "escape")  # drops the narrow; the sort stays
        assert "10 shown / 10 total" in status(app)
        assert "year ↑" in status(app)
        assert titles(app)[0] == "A Neural"  # 2003 first
        assert titles(app)[-1] == "Sparse"  # no year: last, whichever way the sort runs
        assert info_text(app).startswith("Deep Residual")

        await press(pilot, "g", "r", *"lost2015", "enter")
        assert app.current["ref"] == "lost2015"
        assert info_text(app).startswith("A Paper Whose File Went Missing")

        await press(pilot, "/", *"nlp", "enter")
        assert "3 shown" in status(app)
        await press(pilot, "g", "r", *r"\cite{ho2020}", "enter")  # pasted from a .tex, hidden by the narrow
        assert info_text(app).startswith("Denoising Diffusion")
        assert "/ nlp" not in status(app)  # jumping dropped the narrow that hid it
        assert "10 shown / 10 total" in status(app)
        await press(pilot, "q")
    assert app.return_code == 0


async def test_a_forgotten_chord_is_found_by_waiting_on_its_prefix(papis_lib):
    """`g`, then wait: which-key lists what follows; `r` finishes the chord."""
    app = awkward_app(papis_lib)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        await pilot.press("g")
        await pilot.pause(app.keymap.option("which_key_delay_ms") / 1000 + 0.2)
        shown = screen(app)
        assert "─ g ─" in shown
        assert "r  go to a citekey" in shown and "l  switch library" in shown
        assert "g-" in status(app)  # the pending chord, on the right
        await press(pilot, "r", *"ho2020", "enter")
        assert "─ g ─" not in screen(app)
        assert info_text(app).startswith("Denoising Diffusion")


async def test_scope_save_it_wander_off_and_load_it_back(papis_lib):
    app = awkward_app(papis_lib)
    saved = papis_lib / "saved.json"
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "s", *"year:2020", "enter")
        assert "year:2020" in status(app)
        assert "3 shown / 3 total" in status(app)

        await press(pilot, "S", *"title", "enter")
        assert titles(app) == ["Denoising", "Scaling", "深層学習"]
        assert "Title ↑" in lines(app)[1]

        await press(pilot, "\\", "s", *"twenty", "enter")
        assert json.loads(saved.read_text())["twenty"] == {"scope": "year:2020", "sort": "title", "reverse": False}

        await press(pilot, "s", "ctrl+u", "enter")  # the whole library again
        await press(pilot, "S", *"recently", "enter")
        assert "10 shown / 10 total" in status(app)
        assert "year:2020" not in status(app)
        assert titles(app)[0] == "Attention"

        await press(pilot, "g", "s")
        labels = [item.label for item in app.screen.items]
        assert "twenty" in labels and "to-read" in labels  # app-saved and config.toml, side by side
        await press(pilot, *"twenty", "enter")
        assert "year:2020" in status(app)
        assert "title ↑" in status(app)
        assert "3 shown / 3 total" in status(app)
        assert titles(app) == ["Denoising", "Scaling", "深層学習"]
        assert "Scaling Laws [Extended Version]" in row_of(app, "Scaling")

        await press(pilot, "g", "s", *"twenty", "shift+enter")  # forget it
        assert "twenty" not in json.loads(saved.read_text())
        assert "year:2020" in status(app)  # forgetting a search does not leave it
        await press(pilot, "q")
    assert app.return_code == 0


async def test_switch_to_the_second_library_and_back(papis_lib):
    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "space")
        assert "1 marked" in status(app)

        await press(pilot, "g", "l")
        assert sorted(item.label for item in app.screen.items) == ["second", "test"]
        await press(pilot, *"second", "enter")
        shown = "\n".join(list_lines(app))
        assert "Gradient-Based Learning" in shown and "A Fast Learning" in shown
        assert "Attention Is All" not in shown
        assert "2 shown / 2 total" in status(app)
        assert "marked" not in status(app)  # marks belong to the library they were made in
        assert info_text(app).startswith(("Gradient-Based", "A Fast Learning"))

        await press(pilot, "g", "l", *"test", "enter")
        assert "10 shown / 10 total" in status(app)
        assert "Attention Is All" in row_of(app, "Attention")
        assert "Deep Belief" not in screen(app)
        await press(pilot, "q")
    assert app.return_code == 0


async def test_open_the_log_scroll_it_close_it_and_quit_from_it(papis_lib):
    app = awkward_app(papis_lib)
    async with app.run_test(size=(100, 30)) as pilot:
        await settle(pilot)
        # chords that do not exist each leave a line: more than the pane's ten rows hold
        await press(pilot, *(key for letter in "abcdeghjkpqy" for key in ("z", letter)))
        assert not any("─ log ─" in line for line in lines(app))  # nothing opened the log yet

        await press(pilot, "4")
        assert any("─ log ─" in line for line in lines(app))
        assert " LOG " in status(app)
        assert "no binding for z y" in screen(app)  # the newest line is in view
        assert "no binding for z a" not in screen(app)  # ten rows: the oldest has scrolled off

        await press(pilot, "g", "g")
        assert "no binding for z a" in screen(app)
        assert "no binding for z y" not in screen(app)
        await press(pilot, "j", "j")
        assert "no binding for z a" not in screen(app)
        await press(pilot, "G")
        assert "no binding for z y" in screen(app)

        await press(pilot, "escape")
        assert not any("─ log ─" in line for line in lines(app))
        assert " LIST " in status(app)

        await press(pilot, "g", "o")
        assert any("─ log ─" in line for line in lines(app))
        await press(pilot, "q")  # quits from the log pane, not just from the list
    assert app.return_code == 0


async def test_a_line_logged_while_the_log_is_closed_reads_as_one_line(papis_lib):
    """Open the log, close it, do something that logs, open it again. The pane
    is hidden when the line is written, and a hidden widget has no width: this
    once came out one character per row."""
    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot)
        await press(pilot, "4", "escape")  # a look at the log, back to the list
        await press(pilot, "z", "x")  # no such chord: logged while the log is closed
        await press(pilot, "g", "o")
        assert "no binding for z x" in screen(app)
