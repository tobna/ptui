"""Edges of the one writer of `info.yaml`.

Comments around the key that changes, quoting and long lines, and what a failed write leaves behind.
"""

import os

import pytest

from ptui import safewrite


def _write(tmp_path, text, change):
    """Write text, change it through `safewrite`, and read what landed.

    Args:
        tmp_path: pytest's per-test temporary folder.
        text: The starting `info.yaml`.
        change: Mutates the ruamel map.

    Returns:
        The file's text afterwards.
    """
    path = tmp_path / "info.yaml"
    path.write_text(text, encoding="utf-8")
    info = safewrite.read(path)
    change(info.data)
    safewrite.write(info)
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("text", "change", "want"),
    [
        pytest.param(
            "# header\ntitle: T\n# about year\nyear: 2020\n",
            lambda data: safewrite.drop(data, "title"),
            "# header\n# about year\nyear: 2020\n",
            id="drop-first-key-keeps-header-and-next-comment",
        ),
        pytest.param(
            "tags:\n- a\n# about year\nyear: 2020\n",
            lambda data: safewrite.drop(data, "tags"),
            "# about year\nyear: 2020\n",
            id="drop-first-key-that-is-a-list",
        ),
        pytest.param(
            "title: T\n# about year\nyear: 2020 # eol\n",
            lambda data: safewrite.drop(data, "title"),
            "# about year\nyear: 2020 # eol\n",
            id="drop-first-key-keeps-the-next-keys-eol-comment",
        ),
        pytest.param(
            "tags:\n- a\n# about year\nyear: 2020\n",
            lambda data: safewrite.put(data, "tags", []),
            "tags: []\n# about year\nyear: 2020\n",
            id="list-emptied",
        ),
        pytest.param(
            "tags: a\n# about year\nyear: 2020\n",
            lambda data: safewrite.put(data, "tags", ["a", "b"]),
            "tags:\n- a\n- b\n# about year\nyear: 2020\n",
            id="scalar-becomes-list",
        ),
        pytest.param(
            "author_list:\n- family: A\n  given: B\n# about year\nyear: 2020\n",
            lambda data: safewrite.put(data, "author_list", [{"family": "C", "given": "D"}]),
            "author_list:\n- family: C\n  given: D\n# about year\nyear: 2020\n",
            id="list-of-maps-replaced",
        ),
        pytest.param(
            "extra:\n  a: 1\n# about year\nyear: 2020\n",
            lambda data: safewrite.put(data, "extra", {"b": 2}),
            "extra:\n  b: 2\n# about year\nyear: 2020\n",
            id="map-replaced",
        ),
        pytest.param(
            "author_list:\n- family: A\n# about year\nyear: 2020\n# about note\nnote: x\n",
            lambda data: safewrite.drop(data, "year"),
            "author_list:\n- family: A\n# about year\n# about note\nnote: x\n",
            id="drop-after-a-list-of-maps",
        ),
        pytest.param(
            "title: T\nyear: 2020\n# end of file\n",
            lambda data: safewrite.drop(data, "year"),
            "title: T\n# end of file\n",
            id="drop-last-key-keeps-the-closing-comment",
        ),
        pytest.param(
            "year: 2020 # eol\n# after\nnote: x\n",
            lambda data: safewrite.put(data, "year", 2021),
            "year: 2021 # eol\n# after\nnote: x\n",
            id="scalar-replaced-keeps-its-eol-comment",
        ),
        pytest.param(
            "title: T\n# after title\nyear: 2020\n",
            lambda data: safewrite.put(data, "new", "v"),
            "title: T\n# after title\nyear: 2020\nnew: v\n",
            id="new-key-appends-and-moves-nothing",
        ),
        pytest.param(
            "title: T\n",
            lambda data: safewrite.drop(data, "missing"),
            "title: T\n",
            id="dropping-an-absent-key-is-a-no-op",
        ),
    ],
)
def test_comments_stay_where_they_were(tmp_path, text, change, want):
    r"""Comments stay where they were.

    Args:
        tmp_path: pytest's per-test temporary folder.
        text: Parametrized, such as `'# header\ntitle: T\n# about year\nyear: 2020\n'`.
        change: Parametrized, such as `lambda d: safewrite.drop(d, 'title')`.
        want: Parametrized, such as `'# header\n# about year\nyear: 2020\n'`.
    """
    assert _write(tmp_path, text, change) == want


def test_quotes_unicode_and_long_lines_survive_an_unrelated_edit(tmp_path):
    """Quotes unicode and long lines survive an unrelated edit.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    title = "Schrödinger und 日本語 " + "long " * 60
    text = f"title: '{title.strip()}'\nref: \"dq\"\nyear: 2020\n"
    written = _write(tmp_path, text, lambda data: safewrite.put(data, "year", 2021))
    assert written == text.replace("2020", "2021")  # same quotes, no rewrap, no escaping


def test_an_empty_file_can_be_written(tmp_path):
    """An empty file can be written.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    assert _write(tmp_path, "", lambda data: safewrite.put(data, "title", "x")) == "title: x\n"


def test_a_concurrent_write_during_edit_aborts_and_keeps_the_other_writers_bytes(tmp_path, papis_lib):
    """A concurrent write during edit aborts and keeps the other writers bytes.

    `edit` re-stats before replacing: a writer that lands between ptui's read
    and its write must win, not be merged over.

    Args:
        tmp_path: pytest's per-test temporary folder.
        papis_lib: The temporary folder of the `papis_lib` fixture, holding the library in `lib/`.
    """
    import papis.document

    folder = papis_lib / "lib" / "doc0"
    info = folder / "info.yaml"
    doc = papis.document.from_folder(str(folder))

    def racing(data):
        """Change the file on disk while `safewrite` holds it.

        Args:
            data: The ruamel map.
        """
        safewrite.put(data, "year", 1999)
        info.write_text(info.read_text() + "someone_else: 1\n")
        stat = info.stat()  # a distinct mtime, whatever the filesystem's clock granularity
        os.utime(info, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    with pytest.raises(safewrite.StaleError):
        safewrite.edit(doc, racing)
    text = info.read_text()
    assert "someone_else: 1" in text
    assert "year: 2017" in text
    assert not list(folder.glob("*.ptui.tmp"))


def test_a_value_yaml_cannot_represent_leaves_the_file_and_no_tmp(tmp_path):
    """A value yaml cannot represent leaves the file and no tmp.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    path = tmp_path / "info.yaml"
    path.write_text("title: T\nyear: 2020\n")
    info = safewrite.read(path)
    info.data["year"] = object()
    with pytest.raises(Exception, match="cannot represent"):
        safewrite.write(info)
    assert path.read_text() == "title: T\nyear: 2020\n"
    assert not list(tmp_path.glob("*.ptui.tmp"))


def test_a_failed_write_does_not_break_the_next_one(tmp_path):
    """A failed write does not break the next one.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    path = tmp_path / "info.yaml"
    path.write_text("title: T\nyear: 2020\n")
    info = safewrite.read(path)
    info.data["year"] = object()
    with pytest.raises(Exception, match="cannot represent"):
        safewrite.write(info)

    info = safewrite.read(path)
    safewrite.put(info.data, "year", 2021)
    safewrite.write(info)
    assert path.read_text() == "title: T\nyear: 2021\n"
