"""An awkward example library and a look at the screen, for the session tests.

Each `test_session_*.py` drives the real app from startup to quit the way a
person would. They share this library rather than the three tidy documents of
`conftest.papis_lib`: the bugs live in the documents that are not tidy.

Everything lands in the `papis_lib` tmp_path: the documents, a second library
for `g l`, and (through `conftest.build_app`) the trash, pdf root, state file,
saved searches and query history.
"""

import shutil
from pathlib import Path

from conftest import build_app

# Newest first by `time-added`, which is the shipped default sort, so this is
# also the order the rows come up in. Comments and an unknown key ride along in
# the first document: a write through `safewrite` has to keep both.
DOCS = {
    "vaswani": """\
# added by hand; keep this comment
title: Attention Is All You Need
author: Vaswani, Ashish and Shazeer, Noam
author_list:
- family: Vaswani
  given: Ashish
- family: Shazeer
  given: Noam
year: 2017
type: inproceedings
booktitle: Advances in Neural Information Processing Systems
ref: vaswani2017
tags: [transformers, nlp]
x-reviewer: Reviewer Two  # not a papis key
files: [vaswani.pdf]
papis_id: s-vaswani
time-added: 2024-12-01-00:00:00
""",
    "yamada": """\
title: 深層学習による画像認識の研究
author: 山田, 太郎
author_list:
- family: 山田
  given: 太郎
year: 2020
type: article
journal: 情報処理学会論文誌
ref: yamada2020
tags: [cjk]
papis_id: s-yamada
time-added: 2024-11-01-00:00:00
""",
    "devlin": """\
title: '{B}ERT: Pre-training of Deep Bidirectional Transformers for Language Understanding'
author: Devlin, Jacob
author_list:
- family: Devlin
  given: Jacob
year: 2019
type: inproceedings
booktitle: NAACL
doi: 10.18653/v1/N19-1423
ref: devlin2019
tags: [nlp, transformers]
papis_id: s-devlin
time-added: 2024-10-01-00:00:00
""",
    "kaplan": """\
title: Scaling Laws [Extended Version]
author: Kaplan, Jared
author_list:
- family: Kaplan
  given: Jared
year: 2020
type: article
journal: Journal of Scale
doi: 10.1000/scaling
ref: kaplan2020
files: ['scaling[v2].pdf']
papis_id: s-kaplan
time-added: 2024-09-01-00:00:00
""",
    "olshausen": """\
title: Sparse Coding Draft
author: Olshausen, Bruno
author_list:
- family: Olshausen
  given: Bruno
type: misc
ref: olshausen-draft
tags: [draft]
papis_id: s-olshausen
time-added: 2024-08-01-00:00:00
""",
    "anon": """\
title: Benchmark Report Without Authors
year: 2019
type: report
institution: Some Lab
ref: anon2019
papis_id: s-anon
time-added: 2024-07-01-00:00:00
""",
    "he": """\
title: Deep Residual Learning for Image Recognition
author: He, Kaiming
author_list:
- family: He
  given: Kaiming
year: 2016
type: inproceedings
booktitle: CVPR
ref: he2016
files: [resnet.pdf, resnet_supp.pdf]
papis_id: s-he
time-added: 2024-06-01-00:00:00
""",
    "lost": """\
title: A Paper Whose File Went Missing
author: Lost, Lola
author_list:
- family: Lost
  given: Lola
year: 2015
type: article
journal: Journal of Lost Things
ref: lost2015
files: [gone.pdf]
papis_id: s-lost
time-added: 2024-05-01-00:00:00
""",
    "ho": """\
title: Denoising Diffusion Probabilistic Models
author: Ho, Jonathan
author_list:
- family: Ho
  given: Jonathan
year: 2020
type: article
eprint: '2006.11239'
eprinttype: arxiv
url: https://arxiv.org/abs/2006.11239
ref: ho2020
papis_id: s-ho
time-added: 2024-04-01-00:00:00
""",
    "bengio": """\
title: A Neural Probabilistic Language Model
author: Bengio, Yoshua
author_list:
- family: Bengio
  given: Yoshua
year: 2003
type: article
journal: Journal of Machine Learning Research
ref: bengio2003
tags: [nlp, classic]
papis_id: s-bengio
time-added: 2024-03-01-00:00:00
""",
}

FILES = {"vaswani": ["vaswani.pdf"], "kaplan": ["scaling[v2].pdf"], "he": ["resnet.pdf", "resnet_supp.pdf"]}
"""What exists on disk. `lost` names `gone.pdf`, which does not."""

SECOND = {
    "lecun": ("Gradient-Based Learning Applied to Document Recognition", 1998, "LeCun"),
    "hinton": ("A Fast Learning Algorithm for Deep Belief Nets", 2006, "Hinton"),
}

ORDER = ["vaswani", "yamada", "devlin", "kaplan", "olshausen", "anon", "he", "lost", "ho", "bengio"]
"""Folder names in the default sort: newest `time-added` first."""


def awkward_library(papis_lib: Path) -> Path:
    """Replace the fixture's three documents with ours, add a second library."""
    import papis.config
    import papis.database

    lib = papis_lib / "lib"
    shutil.rmtree(lib)
    for name, text in DOCS.items():
        (lib / name).mkdir(parents=True)
        (lib / name / "info.yaml").write_text(text)
        for file in FILES.get(name, []):
            (lib / name / file).write_bytes(f"%PDF {name} {file}".encode())

    second = papis_lib / "second"
    for name, (title, year, author) in SECOND.items():
        (second / name).mkdir(parents=True)
        (second / name / "info.yaml").write_text(
            f"title: {title}\nyear: {year}\nauthor: {author}\n"
            f"author_list:\n- family: {author}\n  given: X\n"
            f"ref: {name}{year}\npapis_id: second-{name}\n"
        )
    config = papis_lib / "config" / "config"
    config.write_text(config.read_text() + f"\n[second]\ndir = {second}\n")
    papis.config.CURRENT_CONFIGURATION = None
    papis.config.CURRENT_LIBRARY = None
    papis.database.clear_cached()
    return lib


def awkward_app(papis_lib: Path, extra: str = ""):
    """The real app over the awkward library. `persist_state` stays off, as
    `build_app` writes it: two sessions in one test must not leak."""
    awkward_library(papis_lib)
    return build_app(papis_lib, extra)


def screen(app) -> str:
    """The composited screen, as `scripts/shot.py --text` prints it — the truth
    about what a person sees, where the SVG export lies about spacing."""
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


def lines(app) -> list[str]:
    return screen(app).splitlines()


def info_text(app) -> str:
    """The info pane's text, wrapped as drawn but without the borders around it."""
    return str(app.query_one("#info").render())


def status(app) -> str:
    """The status bar: the bottom line, while no prompt is open below it."""
    return lines(app)[-1]


def hints(app) -> str:
    return lines(app)[-2]


def list_lines(app) -> list[str]:
    """The list pane alone, cropped by cells — a CJK title is two per character,
    and side by side the info pane's lines share each screen line with a row."""
    region = app.query_one("#list-pane").region
    strips = app.screen._compositor.render_strips()[region.y : region.bottom]
    return [strip.crop(region.x, region.right).text for strip in strips]


def row_of(app, needle: str) -> str:
    """The one row of the list pane holding `needle`."""
    found = [line for line in list_lines(app) if needle in line]
    assert len(found) == 1, f"{needle!r} on {len(found)} rows:\n{screen(app)}"
    return found[0]
