"""Shared by the `test_cover_actions_*` files: the fixture library's documents,
files attached the way a user's script would, and a log that can be read back."""

from pathlib import Path

from ptui import library


def info(papis_lib: Path, folder: str = "doc0") -> Path:
    return papis_lib / "lib" / folder / "info.yaml"


def attach(papis_lib: Path, folder: str, *names: str) -> None:
    """Create each file in the document folder and append it to `files`."""
    path = info(papis_lib, folder)
    text = path.read_text()
    if "files:" not in text:
        text += "files:\n"
    for name in names:
        (path.parent / name).write_bytes(name.encode())
        text += f"- {name}\n"
    path.write_text(text)


def doc(app, papis_id: str):
    return next(d for d in app.docs if library.doc_id(d) == papis_id)


def logs(app, monkeypatch) -> list[str]:
    """Every `log_line` from here on, as the call site wrote it."""
    logged: list[str] = []
    monkeypatch.setattr(app, "log_line", logged.append)
    return logged
