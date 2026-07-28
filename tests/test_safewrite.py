import pytest

from ptui import safewrite

SAMPLE = """\
# a comment my scripts rely on
title: 'Attention Is All You Need'
year: 2017
tags: [nlp, transformers]
my_custom_key:
  nested: value   # trailing comment
files:
- ../../pdfs/vaswani.pdf
"""


def _info(tmp_path):
    path = tmp_path / "info.yaml"
    path.write_text(SAMPLE)
    return path


def test_round_trip_preserves_unknown_keys_and_comments(tmp_path):
    path = _info(tmp_path)
    info = safewrite.read(path)
    info.data["tags"].append("attention")
    safewrite.write(info)

    text = path.read_text()
    assert "# a comment my scripts rely on" in text
    assert "# trailing comment" in text
    assert "my_custom_key" in text
    assert "../../pdfs/vaswani.pdf" in text  # relative path style kept verbatim
    assert text.index("title") < text.index("year")  # key order kept
    assert "attention" in text


def test_write_refuses_when_the_file_changed(tmp_path):
    path = _info(tmp_path)
    info = safewrite.read(path)
    path.write_text(SAMPLE + "someone_else: 1\n")
    os_mtime = path.stat().st_mtime_ns
    info.data["year"] = 2018

    with pytest.raises(safewrite.StaleError):
        safewrite.write(info)
    assert path.stat().st_mtime_ns == os_mtime
    assert "someone_else" in path.read_text()  # untouched, not merged over


def test_no_tmp_file_left_behind(tmp_path):
    path = _info(tmp_path)
    info = safewrite.read(path)
    safewrite.write(info)
    safewrite.write(info)  # second write uses the refreshed mtime
    assert not list(tmp_path.glob("*.ptui.tmp"))
