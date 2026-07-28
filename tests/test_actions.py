from ptui import actions, clip

from conftest import press, settle


def _attach(papis_lib, name: str = "downloaded paper.pdf", body: bytes = b"pdf") -> None:
    """Give doc0 a file, the way a user's script would: appended to `files`."""
    folder = papis_lib / "lib" / "doc0"
    (folder / name).write_bytes(body)
    info = folder / "info.yaml"
    text = info.read_text()
    info.write_text(text + f"- {name}\n" if "files:" in text else text + f"files:\n- {name}\n")


async def test_relocate_moves_files_and_rewrites_info_yaml(app, papis_lib, monkeypatch):
    _attach(papis_lib)
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "r")
        await settle(pilot)

    dest = papis_lib / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    assert dest.read_bytes() == b"pdf"
    assert not (papis_lib / "lib" / "doc0" / "downloaded paper.pdf").exists()

    info = (papis_lib / "lib" / "doc0" / "info.yaml").read_text()
    assert str(dest) in info
    assert "papis_id: id0" in info  # the rest of the file survived


async def test_relocate_reports_conflicts_without_touching_anything(app, papis_lib):
    _attach(papis_lib)
    (papis_lib / "pdfs").mkdir()
    clash = papis_lib / "pdfs" / "2017_Vaswani_Attention_Is_All_You_Need.pdf"
    clash.write_bytes(b"a different paper")

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "f", "r")
        await settle(pilot)

    assert clash.read_bytes() == b"a different paper"
    assert (papis_lib / "lib" / "doc0" / "downloaded paper.pdf").exists()
    assert "files:" in (papis_lib / "lib" / "doc0" / "info.yaml").read_text()


async def test_citekey_yank_is_batch_aware(app, monkeypatch):
    yanked = []
    monkeypatch.setattr(clip, "copy", lambda app, text: yanked.append(text) or "test")
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "y", "y")
        assert yanked == ["\\cite{Vaswani2017}"]
        await press(pilot, "space", "space")  # mark two documents
        await press(pilot, "y", "y")
        assert yanked[-1] == "\\cite{Vaswani2017} \\cite{He2016}"


async def test_main_file_comes_before_notes(app, papis_lib):
    _attach(papis_lib, "paper.pdf")
    _attach(papis_lib, "paper_notes.pdf")
    async with app.run_test() as pilot:
        await settle(pilot)
        names = [p.name for p in actions.files_of(app, app.current)]
    assert names == ["paper.pdf", "paper_notes.pdf"]


async def test_open_reports_a_missing_file_instead_of_launching(app, papis_lib, monkeypatch):
    import papis.utils

    _attach(papis_lib)
    (papis_lib / "lib" / "doc0" / "downloaded paper.pdf").unlink()
    opened = []
    monkeypatch.setattr(papis.utils, "open_file", lambda *a, **k: opened.append(a))
    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "o")
    assert opened == []
