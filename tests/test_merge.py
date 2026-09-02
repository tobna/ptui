import papis.document

from ptui import merge


def docs(*data):
    return [papis.document.from_data(d) for d in data]


PUBLISHED = {
    "ref": "he2016deep",
    "author_list": [{"family": "He", "given": "Kaiming"}],
    "title": "Deep Residual Learning",
    "year": 2016,
    "type": "inproceedings",
    "booktitle": "CVPR",
    "doi": "10.1109/CVPR.2016.90",
    "papis_id": "id-published",
    "time-added": "2024-01-01-00:00:00",
}
PREPRINT = {
    "ref": "he2015arxiv",
    "author_list": [{"family": "He", "given": "Kaiming"}],
    "title": "Deep Residual Learning",
    "year": 2015,
    "type": "article",
    "eprint": "1512.03385",
    "abstract": "Deeper neural networks are more difficult to train.",
    "papis_id": "id-preprint",
    "time-added": "2023-06-01-00:00:00",
}


def test_survivor_choices_are_the_distinct_refs():
    published, preprint = docs(PUBLISHED, PREPRINT)
    choices = merge.survivor_choices([published, preprint])
    assert [ref for ref, _ in choices] == ["he2016deep", "he2015arxiv"]
    # picking the ref picks the document, which is the whole point
    assert choices[0][1]["papis_id"] == "id-published"

    # no ref at all still has to be selectable
    (untitled,) = docs({"title": "No Ref Here"})
    assert merge.survivor_choices([untitled])[0][0] == "(no ref) No Ref Here"


def test_plan_separates_gaps_from_real_clashes():
    published, preprint = docs(PUBLISHED, PREPRINT)
    plan = merge.plan(published, [preprint])

    # only the preprint had these — nothing to ask
    assert plan.gaps == {"eprint": "1512.03385", "abstract": PREPRINT["abstract"]}
    # both had these and they differ
    assert set(plan.clashes) == {"year", "type"}
    assert plan.clashes["year"] == [2016, 2015]  # survivor's value first
    # agreed and skipped keys are neither
    assert "title" not in plan.clashes and "title" not in plan.gaps
    assert "ref" not in plan.clashes  # decided by which document survives
    assert "papis_id" not in plan.clashes
    # the document has existed since the earliest copy
    assert plan.time_added == "2023-06-01-00:00:00"


def test_resolve_keeps_the_survivor_where_nothing_was_answered():
    published, preprint = docs(PUBLISHED, PREPRINT)
    plan = merge.plan(published, [preprint])

    data = merge.resolve(plan, {})
    assert data["eprint"] == "1512.03385"  # gaps are filled regardless
    assert "year" not in data  # unanswered clash keeps the survivor's own value
    assert data["time-added"] == "2023-06-01-00:00:00"

    answered = merge.resolve(plan, {"year": 2015, "type": "article"})
    assert answered["year"] == 2015
    assert answered["type"] == "article"


def test_files_are_never_a_clash_and_carry_their_document():
    a, b = docs({**PUBLISHED, "files": ["paper.pdf"]}, {**PREPRINT, "files": ["arxiv.pdf"]})
    plan = merge.plan(a, [b])
    assert "files" not in plan.clashes and "files" not in plan.gaps
    # the document comes along because the entry may be relative to its own folder
    assert merge.file_entries([a, b]) == [(a, "paper.pdf"), (b, "arxiv.pdf")]


def test_a_blank_value_is_not_a_clash():
    a, b = docs({"ref": "a", "title": "T", "doi": "10.1/x"}, {"ref": "b", "title": "T", "doi": ""})
    plan = merge.plan(a, [b])
    assert plan.clashes == {}
    assert plan.gaps == {}


def test_three_documents_offer_every_distinct_value():
    a, b, c = docs(
        {"ref": "a", "title": "T", "year": 2016},
        {"ref": "b", "title": "T", "year": 2015},
        {"ref": "c", "title": "T", "year": 2014},
    )
    plan = merge.plan(a, [b, c])
    assert plan.clashes["year"] == [2016, 2015, 2014]


def two_copies(tmp_path):
    """A published record and its arXiv preprint, both inside the fixture library."""
    lib = tmp_path / "lib"
    for name, data, pdf in (
        ("published", PUBLISHED, "paper.pdf"),
        ("preprint", PREPRINT, "arxiv.pdf"),
    ):
        folder = lib / name
        folder.mkdir()
        (folder / pdf).write_bytes(b"%PDF-1.7\n" + name.encode())
        lines = [f"{k}: {v!r}" if isinstance(v, str) else f"{k}: {v}" for k, v in data.items() if k != "author_list"]
        lines.append("author_list:\n- family: He\n  given: Kaiming")
        (folder / "info.yaml").write_text("# keep me\n" + "\n".join(lines) + f"\nfiles:\n- {pdf}\n")
    import papis.database

    papis.database.clear_cached()
    return lib / "published", lib / "preprint"


async def test_merge_folds_marks_into_the_chosen_ref(app, tmp_path):
    from conftest import press, settle

    from ptui import actions, library, ui

    published, preprint = two_copies(tmp_path)
    async with app.run_test(size=(120, 30)) as pilot:
        actions.reload(app)
        await settle(pilot)
        # by ref: the fixture library already ships a "Deep Residual Learning"
        ours = {"he2016deep", "he2015arxiv"}
        copies = [d for d in app.docs if d.get("ref") in ours]
        assert len(copies) == 2
        app.marks = {library.doc_id(d) for d in copies}

        await press(pilot, "m", "m")
        assert isinstance(app.screen, ui.SelectList)
        # keep the published one: its ref decides who survives
        index = next(i for i, it in enumerate(app.screen.items) if it.label == "he2016deep")
        app.screen.query_one("OptionList").highlighted = index
        await press(pilot, "enter")

        # `year` and `type` clash; take the survivor's value for both
        for _ in range(2):
            assert isinstance(app.screen, ui.SelectList), "expected a conflict picker"
            await press(pilot, "enter")
        await settle(pilot)

        survivors = [d for d in app.docs if d.get("ref") in ours]
        assert len(survivors) == 1
        kept = survivors[0]
        assert kept["ref"] == "he2016deep"
        assert kept["papis_id"] == "id-published"
        assert kept["year"] == 2016  # survivor's value, chosen
        assert kept["eprint"] == "1512.03385"  # gap filled from the preprint
        assert kept["abstract"].startswith("Deeper")
        assert kept["time-added"] == "2023-06-01-00:00:00"  # earliest of the group
        assert len(kept.get("files") or []) == 2  # files unioned, never chosen
        assert "# keep me" in (published / "info.yaml").read_text()  # round-trip intact

        assert not preprint.exists()  # folded in, folder removed from the library
        trash = app.cfg.as_path("undo.trash_dir")
        assert (trash / "preprint").is_dir()  # recoverable, not destroyed
        assert (trash / "preprint" / "info.yaml").exists()

        # the PDF is not in the trashed folder because place() moved it to
        # pdf_root — what matters is that no attachment was lost, so both
        # entries still resolve to a real file
        from ptui import place

        resolved = [place.resolve(kept, entry) for entry in kept["files"]]
        assert all(path.exists() for path in resolved), resolved
        assert {path.read_bytes().split(b"\n")[1] for path in resolved} == {
            b"published",
            b"preprint",
        }
        assert app.marks == set()


async def test_merge_needs_two_marks(app):
    from conftest import press, settle

    async with app.run_test() as pilot:
        await settle(pilot)
        await press(pilot, "space")  # one mark only
        await press(pilot, "m", "m")
        from ptui import ui

        assert not isinstance(app.screen, ui.SelectList)  # nothing opened
