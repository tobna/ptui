"""Sessions that read the awkward library, at the sizes people really run.

Nothing here writes: the point is that what a person sees is the document as
typed — brackets, CJK, LaTeX, gaps — cut where it has to be, never missing.
"""

import pytest
from conftest import press, settle
from session_lib import ORDER, awkward_app, hints, info_text, lines, list_lines, row_of, screen, status

TITLES = {
    "vaswani": "Attention Is All You Need",
    "yamada": "深層学習による画像認識の研究",
    "devlin": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
    "kaplan": "Scaling Laws [Extended Version]",
    "olshausen": "Sparse Coding Draft",
    "anon": "Benchmark Report Without Authors",
    "he": "Deep Residual Learning for Image Recognition",
    "lost": "A Paper Whose File Went Missing",
    "ho": "Denoising Diffusion Probabilistic Models",
    "bengio": "A Neural Probabilistic Language Model",
}
"""Each title as the list should draw it: `{B}ERT` de-braced (SPEC: render
de-braced per `list.strip_latex`), everything else verbatim."""


@pytest.mark.parametrize(
    ("size", "side_by_side", "tags_column"),
    [((80, 24), False, False), ((120, 40), True, False), ((200, 50), True, True)],
)
async def test_every_awkward_title_is_on_screen_whole_or_cut_with_an_ellipsis(
    papis_lib, size, side_by_side, tags_column
):
    from ptui import ui

    app = awkward_app(papis_lib)
    async with app.run_test(size=size) as pilot:
        await settle(pilot)
        shown = screen(app)
        assert app.side_by_side is side_by_side
        assert ("Tags" in lines(app)[1]) is tags_column  # optional: only once the title has its room

        for name in ORDER:
            title = TITLES[name]
            row = next(line for line in list_lines(app) if title[:12] in line)
            # cut by cells at a word, never dropped and never past the border
            cut = row.split(title[:12], 1)[1].split("  ")[0]
            drawn = title[:12] + cut
            assert drawn == title or (drawn.endswith("…") and title.startswith(drawn[:-1])), drawn

        assert "{B}ERT" not in shown
        assert "None" not in shown  # no year, no author: blank, not a Python repr
        assert "{doc" not in shown  # nor a format string papis failed to fill
        assert ui.glyph("type.preprint") in row_of(app, "Denoising Diffusion")  # arXiv-only: a preprint
        assert ui.glyph("type.preprint") not in row_of(app, "Neural Probabilistic")
        assert row_of(app, "Sparse Coding Draft").split("Sparse")[0].strip("│ ").split()[-1] == "Olshau…"

        # The chrome is whole at every size: the status bar's counts, the first hints.
        assert " LIST " in status(app)
        assert "time-added ↓" in status(app)
        assert status(app).rstrip().endswith("10 shown / 10 total")
        assert "enter open file" in hints(app)
        assert "/ narrow" in hints(app)
        await press(pilot, "q")
    assert app.return_code == 0


async def test_walking_the_list_shows_each_awkward_document_in_the_info_pane(papis_lib):
    """j through the whole library, reading the info pane as you go."""
    from ptui import ui

    app = awkward_app(papis_lib)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        seen = {}
        for name in ORDER:
            seen[name] = info_text(app)
            await press(pilot, "j")
        assert app.current["ref"] == "bengio2003"  # j stops at the last row

        assert seen["vaswani"].startswith("Attention Is All You Need")
        assert "Reviewer Two" not in seen["vaswani"]  # an unknown key is kept on disk, not shown
        assert "深層学習による画像認識の研究" in seen["yamada"]
        assert "山田" in seen["yamada"] and "情報処理学会論文誌" in seen["yamada"]
        assert seen["devlin"].startswith("BERT: Pre-training")

        # brackets as typed, in the title and in the file name
        assert seen["kaplan"].startswith("Scaling Laws [Extended Version]")
        assert "scaling[v2].pdf" in seen["kaplan"]

        assert "    year  " not in seen["olshausen"]  # no year: no line for it, not "None"
        assert "  author  " not in seen["anon"]
        assert "    year  2019" in seen["anon"]  # the label as it is drawn, so the checks above mean something

        assert "files (2)" in seen["he"]
        assert "resnet.pdf" in seen["he"] and "resnet_supp.pdf" in seen["he"]

        lost = next(line for line in seen["lost"].splitlines() if "gone.pdf" in line)
        assert ui.glyph("warning") in lost  # the missing file is flagged
        ok = next(line for line in seen["he"].splitlines() if "resnet.pdf" in line)
        assert ui.glyph("warning") not in ok

        assert "Denoising Diffusion" in seen["ho"] and "arxiv.org/abs/2006.11239" in seen["ho"]

        await press(pilot, "g", "g")
        assert app.current["ref"] == "vaswani2017"
        assert info_text(app).startswith("Attention Is All You Need")


async def test_a_terminal_resized_mid_session_keeps_the_cursor_and_the_whole_status_bar(papis_lib):
    app = awkward_app(papis_lib)
    async with app.run_test(size=(200, 50)) as pilot:
        await settle(pilot)
        await press(pilot, "j", "j", "j")  # on the bracketed title
        assert app.side_by_side
        for width, height, side in ((80, 24, False), (120, 40, True), (200, 50, True), (80, 24, False)):
            await pilot.resize_terminal(width, height)
            await settle(pilot)
            assert app.side_by_side is side
            assert app.current["ref"] == "kaplan2020"
            assert info_text(app).startswith("Scaling Laws [Extended Version]")
            assert screen(app).count("Scaling Laws [Extended Version]") == 2  # its row, and the info pane
            assert status(app).rstrip().endswith("10 shown / 10 total")
            assert len(lines(app)) == height


async def test_the_counts_stay_on_the_status_bar_at_80_columns(papis_lib):
    """SPEC: the bar shows scope and narrow, and the counts. Short values, a
    common terminal: the right-hand group simply runs off the edge."""
    app = awkward_app(papis_lib)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        await press(pilot, "s", *"year:2020", "enter", "/", *"scaling", "enter", "space")
        assert "year:2020" in status(app) and "/ scaling" in status(app)
        assert "1 marked" in status(app)
        assert "1 shown / 3 total" in status(app)


async def test_a_crowded_status_bar_gives_up_the_sort_then_cuts_the_longer_query(papis_lib):
    """What yields, in order, so the counts never do: the sort block, then the
    queries with an ellipsis (the longer first), then the left group."""
    app = awkward_app(papis_lib)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(pilot)
        await press(pilot, "s", *"year:2020", "enter")
        assert "time-added ↓" in status(app)  # room for everything yet
        await press(pilot, "space", "/", *"scalinglawsforneurallanguagemodels", "enter")
        bar = status(app)
        assert "time-added" not in bar
        assert "year:2020" in bar  # the shorter query is whole
        assert "/ scalinglaws" in bar and "…" in bar  # the longer one is cut
        assert bar.rstrip().endswith("1 marked (0 visible) 0 shown / 3 total")
        await pilot.resize_terminal(50, 24)  # room for the counts and the mode, no more
        await settle(pilot)
        assert status(app).rstrip().endswith("1 marked (0 visible) 0 shown / 3 total")
        assert " LIST " in status(app) and "year:2020" not in status(app)
