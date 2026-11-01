"""Edges of merging: what counts as blank, what is never asked, and that a
loser's attachment survives its folder going to the trash."""

import papis.document
import pytest
from conftest import settle

from ptui import merge, place


def _doc(**data):
    return papis.document.from_data(data)


def test_blank_values_are_gaps_zero_is_a_value_and_private_keys_are_ignored():
    survivor = _doc(title="T", tags=[], rating=0, note="")
    other = _doc(title="T", tags=["x"], rating=3, note="n", _private="p")
    plan = merge.plan(survivor, [other])
    assert plan.gaps == {"tags": ["x"], "note": "n"}
    assert plan.clashes == {"rating": [0, 3]}  # 0 is a rating, not an empty field


def test_resolve_ignores_answers_to_questions_never_asked_and_keeps_the_earliest_stamp():
    survivor = _doc(title="T", year=2017, **{"time-added": "2020-01-01-00:00:00"})
    other = _doc(title="T", year=2016, **{"time-added": "2024-01-01-00:00:00"})
    plan = merge.plan(survivor, [other])
    assert merge.resolve(plan, {"title": "hijacked", "year": 2016}) == {"year": 2016}  # survivor already earliest


def test_choosing_an_author_list_rewrites_author_to_match():
    survivor = _doc(author_list=[{"family": "A", "given": "B"}], author="A, B")
    other = _doc(author_list=[{"family": "C", "given": "D"}], author="C, D")
    plan = merge.plan(survivor, [other])
    assert plan.questions == ["author_list"]  # `author` is not asked a second time
    assert merge.resolve(plan, {"author_list": other["author_list"]})["author"] == "C, D"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a survivor with `author` but no `author_list` gets the loser's authors as a silent gap; "
    "resolve() then overwrites the survivor's `author` without asking",
)
def test_an_unanswered_merge_does_not_replace_the_survivors_author():
    survivor = _doc(title="T", author="Smith, J.")
    other = _doc(title="T", author_list=[{"family": "Jones", "given": "X"}], author="Jones, X")
    plan = merge.plan(survivor, [other])
    resolved = merge.resolve(plan, {})
    assert plan.questions or resolved.get("author", "Smith, J.") == "Smith, J."


def test_survivor_choices_collapse_one_ref_and_name_a_blank_one_by_title():
    first, twin, blank = _doc(ref="r", title="1"), _doc(ref="r", title="2"), _doc(ref="  ", title="Untitled")
    choices = merge.survivor_choices([first, twin, blank])
    assert [ref for ref, _ in choices] == ["r", "(no ref) Untitled"]
    assert choices[0][1] is first  # the first document holding a ref is the one it keeps


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a loser's file under an in-place rule (every shipped rule) is not added to the survivor's "
    "`files`; it goes to the trash with the loser's folder",
)
async def test_a_losers_file_under_an_in_place_rule_stays_attached(app, papis_lib):
    from ptui import actions

    loser_folder = papis_lib / "lib" / "doc1"
    (loser_folder / "supplement.djvu").write_bytes(b"djvu")  # `*` -> the in-place rule
    info = loser_folder / "info.yaml"
    info.write_text(info.read_text() + "files:\n- supplement.djvu\n")

    async with app.run_test() as pilot:
        actions.reload(app, rescan=True)
        await settle(pilot)
        survivor = next(d for d in app.docs if d["papis_id"] == "id0")
        loser = next(d for d in app.docs if d["papis_id"] == "id1")
        actions._merge_apply(app, merge.plan(survivor, [loser]), {})
        await settle(pilot)
        kept = next(d for d in app.docs if d["papis_id"] == "id0")
        held = [place.resolve(kept, entry) for entry in kept.get("files") or []]
        assert any(path.is_file() and path.read_bytes() == b"djvu" for path in held), held
