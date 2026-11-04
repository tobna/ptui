"""Helpers shared by the `test_cover_actions_*` files.

The fixture library's documents, files attached the way a user's script would, and a log that can be
read back.
"""

from pathlib import Path

from ptui import library


def info(papis_lib: Path, folder: str = "doc0") -> Path:
    """Find a document's `info.yaml`.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture.
        folder: The document's folder name.

    Returns:
        The file's path.
    """
    return papis_lib / "lib" / folder / "info.yaml"


def attach(papis_lib: Path, folder: str, *names: str) -> None:
    """Create each file in the document folder and append it to `files`.

    Args:
        papis_lib: The temporary folder of the `papis_lib` fixture.
        folder: The document's folder name.
        *names: The file names to create.
    """
    path = info(papis_lib, folder)
    text = path.read_text()
    if "files:" not in text:
        text += "files:\n"
    for name in names:
        (path.parent / name).write_bytes(name.encode())
        text += f"- {name}\n"
    path.write_text(text)


def doc(app, papis_id: str):
    """Find a document of the scoped set.

    Args:
        app: The running `PtuiApp`.
        papis_id: The document's id.

    Returns:
        The document; `StopIteration` when the scope lacks it.
    """
    return next(listed for listed in app.docs if library.doc_id(listed) == papis_id)


def logs(app, monkeypatch) -> list[str]:
    """Every `log_line` from here on, as the call site wrote it.

    Args:
        app: The running `PtuiApp`.
        monkeypatch: pytest's monkeypatch fixture.

    Returns:
        A list that fills up as the app logs.
    """
    logged: list[str] = []
    monkeypatch.setattr(app, "log_line", logged.append)
    return logged
