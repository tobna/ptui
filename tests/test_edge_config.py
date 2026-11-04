"""Edges of `config.repair` and unknown-key detection.

Wrong types that look right (a bool is an int, `2.0` is not a line count) and odd column entries.
"""

import pytest

from ptui import config


def _load(tmp_path, text):
    """Write a config file and load it.

    Args:
        tmp_path: pytest's per-test temporary folder.
        text: The TOML.

    Returns:
        The loaded `Config`.
    """
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
    r"""Numbers of the wrong kind are repaired.

    Args:
        tmp_path: pytest's per-test temporary folder.
        text: Parametrized, such as `'[ui]\nsplit_ratio = true\n'`.
        key: Parametrized, such as `'ui.split_ratio'`.
        want: Parametrized, such as `0.6`.
        repaired: Parametrized, such as `True`.
    """
    configuration = _load(tmp_path, text)
    assert configuration.get(key) == want
    assert bool(configuration.invalid) == repaired


def test_odd_columns_are_dropped_or_given_defaults(tmp_path):
    """Odd columns are dropped or given defaults.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    configuration = _load(
        tmp_path,
        'columns = "not here"\n'  # top level: an unknown key, not the list's columns
        '[[list.columns]]\nformat = "{doc[year]}"\nwidth = -1\n'
        '[[list.columns]]\nformat = "{doc[title]}"\nwidth = true\n'
        "[[list.columns]]\nformat = 3\n",
    )
    assert configuration.get("list.columns") == [
        {"format": "{doc[year]}", "width": 0, "title": ""},
        {"format": "{doc[title]}", "width": 0, "title": ""},
    ]
    assert len(configuration.invalid) == 3
    assert configuration.unknown == ("columns",)


def test_a_column_list_that_is_not_a_list_falls_back_to_the_shipped_columns(tmp_path):
    """A column list that is not a list falls back to the shipped columns.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    configuration = _load(tmp_path, '[list]\ncolumns = "x"\n')
    assert configuration.get("list.columns") == config.load(tmp_path / "none.toml").get("list.columns")


def test_unknown_keys_are_reported_dotted_but_not_inside_lists(tmp_path):
    """Unknown keys are reported dotted but not inside lists.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    configuration = _load(tmp_path, '[ui]\nlayuot = "auto"\n[[list.columns]]\nformat = "{doc[title]}"\ntypo = 1\n')
    assert configuration.unknown == ("ui.layuot",)


def test_a_section_given_as_a_scalar_is_repaired_or_refused_by_name(tmp_path):
    """A section given as a scalar is repaired or refused by name.

    Args:
        tmp_path: pytest's per-test temporary folder.
    """
    try:
        configuration = _load(tmp_path, 'ui = "dark"\n')
    except ValueError:
        return  # refused with a message naming the file: acceptable
    assert configuration.get("ui.layout") == "auto"
