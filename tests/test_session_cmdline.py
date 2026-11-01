"""Sessions on the `:` line: commands run by name, with typed arguments."""

import yaml
from conftest import press, settle
from session_lib import awkward_app, info_text, lines, list_lines, row_of, status


def on_disk(folder) -> dict:
    return yaml.safe_load((folder / "info.yaml").read_text())


async def test_cmdline_sorts_sets_a_bracketed_value_and_refuses_a_bad_number(papis_lib):
    app = awkward_app(papis_lib)
    folder = papis_lib / "lib" / "olshausen"
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, ":", *"sort.by", "enter")
        await press(pilot, *"year false", "enter")  # positional, and `false` is a bool
        assert "Year ↑" in lines(app)[1]
        assert "year ↑" in status(app)
        assert "A Neural Probabilistic" in list_lines(app)[2]  # 2003, the oldest, on top

        await press(pilot, "g", "r", *"olshausen-draft", "enter")
        await press(pilot, ":", *"doc.set", "enter")
        await press(pilot, *'url "https://example.org/[draft] v2"', "enter")  # quoted: one argument
        assert on_disk(folder)["url"] == "https://example.org/[draft] v2"
        assert "https://example.org/[draft] v2" in info_text(app)

        await press(pilot, ":", *"doc.rating", "enter", *"lots", "enter")
        assert "rating" not in on_disk(folder)  # not a number: nothing written
        await press(pilot, "4")
        assert any("lots" in line for line in lines(app))  # and the log says why

        await press(pilot, "u")  # undoes the url, the last thing that did write
        assert "url" not in on_disk(folder)
        assert "example.org" not in info_text(app)
        await press(pilot, "q")
    assert app.return_code == 0


async def test_move_a_document_to_the_second_library_and_back(papis_lib):
    """`:doc.move second` has no key; it is the `:` line or nothing."""
    app = awkward_app(papis_lib)
    here, there = papis_lib / "lib" / "anon", papis_lib / "second" / "anon"
    before = (here / "info.yaml").read_text()
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot)
        await press(pilot, "g", "r", *"anon2019", "enter")
        await press(pilot, ":", *"doc.move", "enter", *"second", "enter")
        assert not here.exists() and (there / "info.yaml").exists()
        assert "9 shown / 9 total" in status(app)
        assert not any("Benchmark Report" in line for line in list_lines(app))

        await press(pilot, "g", "l", *"second", "enter")
        assert "3 shown / 3 total" in status(app)  # listed once, not twice
        await press(pilot, "g", "r", *"anon2019", "enter")
        assert "Benchmark Report Without Authors" in row_of(app, "Benchmark Report")

        await press(pilot, ":", *"doc.move", "enter", *"test", "enter")
        assert (here / "info.yaml").read_text() == before
        assert "2 shown / 2 total" in status(app)
        await press(pilot, "g", "l", *"test", "enter")
        assert "10 shown / 10 total" in status(app)
        assert "Benchmark Report Without Authors" in row_of(app, "Benchmark Report")
        await press(pilot, "q")
    assert app.return_code == 0
