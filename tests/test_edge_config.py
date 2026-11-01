"""Edges of `config.repair` and unknown-key detection: wrong types that look
right (a bool is an int, `2.0` is not a line count) and odd column entries."""

import pytest

from ptui import config


def _load(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return config.load(path)


@pytest.mark.parametrize(
    ("text", "key", "want", "repaired"),
    [
        ("[ui]\nsplit_ratio = true\n", "ui.split_ratio", 0.6, True),  # a bool is an int to Python
        ('[ui]\nsplit_ratio = "0.5"\n', "ui.split_ratio", 0.6, True),
        ("[ui]\nsplit_ratio = 1\n", "ui.split_ratio", 0.6, True),
        ("[ui]\nsplit_ratio = 0.1\n", "ui.split_ratio", 0.1, False),  # the bounds are allowed
        ("[ui]\nsplit_ratio = 0.9\n", "ui.split_ratio", 0.9, False),
        ("[list]\nrow_height = 2.0\n", "list.row_height", 1, True),
        ("[list]\nrow_height = true\n", "list.row_height", 1, True),
        ("[list]\nrow_height = 3\n", "list.row_height", 3, False),
    ],
)
def test_numbers_of_the_wrong_kind_are_repaired(tmp_path, text, key, want, repaired):
    cfg = _load(tmp_path, text)
    assert cfg.get(key) == want
    assert bool(cfg.invalid) == repaired


def test_odd_columns_are_dropped_or_given_defaults(tmp_path):
    cfg = _load(
        tmp_path,
        'columns = "not here"\n'  # top level: an unknown key, not the list's columns
        '[[list.columns]]\nformat = "{doc[year]}"\nwidth = -1\n'
        '[[list.columns]]\nformat = "{doc[title]}"\nwidth = true\n'
        "[[list.columns]]\nformat = 3\n",
    )
    assert cfg.get("list.columns") == [
        {"format": "{doc[year]}", "width": 0, "title": ""},
        {"format": "{doc[title]}", "width": 0, "title": ""},
    ]
    assert len(cfg.invalid) == 3
    assert cfg.unknown == ("columns",)


def test_a_column_list_that_is_not_a_list_falls_back_to_the_shipped_columns(tmp_path):
    cfg = _load(tmp_path, '[list]\ncolumns = "x"\n')
    assert cfg.get("list.columns") == config.load(tmp_path / "none.toml").get("list.columns")


def test_unknown_keys_are_reported_dotted_but_not_inside_lists(tmp_path):
    cfg = _load(tmp_path, '[ui]\nlayuot = "auto"\n[[list.columns]]\nformat = "{doc[title]}"\ntypo = 1\n')
    assert cfg.unknown == ("ui.layuot",)


@pytest.mark.xfail(
    strict=True,
    reason='BUG: a section set to a scalar (`ui = "dark"`) makes repair() assign into a str and '
    "raise TypeError; the CLI only turns ValueError into a message, so ptui dies with a traceback",
)
def test_a_section_given_as_a_scalar_is_repaired_or_refused_by_name(tmp_path):
    try:
        cfg = _load(tmp_path, 'ui = "dark"\n')
    except ValueError:
        return  # refused with a message naming the file: acceptable
    assert cfg.get("ui.layout") == "auto"
