"""Print the glyph table and the candidates for it, so your own font decides.

    uv run python scripts/glyphs.py

Every glyph is printed inside brackets. `[x]` is exactly three cells wide, so a
glyph your font lacks — or renders double-width — pushes the closing bracket and
the columns after it out of line. That ragged edge is the whole test: rich's
`cell_len` says what ptui *assumed*, your terminal shows what actually happened,
and a disagreement between the two is the bug.

Codepoints are all in the Font Awesome 4 range (U+F000-U+F2FF), which sits in
the same place in Nerd Fonts v2 and v3. If a glyph here is missing, suspect the
patch level of the font before suspecting the codepoint.

Nothing here is part of the app. Candidates live in this file until one is
chosen and moved into `ui.GLYPHS`.
"""

from __future__ import annotations

from rich.cells import cell_len

from ptui import config, ui

# Proposals, not decisions. Several per slot on purpose: pick the one that reads
# at one cell in your font, which is not always the one that sounds right.
CANDIDATES: dict[str, list[tuple[str, str]]] = {
    "document type — preprint (arXiv-only article)": [
        ("\uf1d8", "fa-paper-plane, sent out and not landed - in use"),
        ("\u03c7", "GREEK SMALL CHI - the X in the arXiv logo, and in every font"),
        ("\uf016", "fa-file-o, a page nobody has stamped"),
        ("\uf017", "fa-clock-o, still waiting"),
        ("\uf0ee", "fa-cloud-upload, uploaded somewhere"),
        ("\U000f0b09", "nf-md-alpha-x-box - NERD FONTS v3 ONLY, blank on a v2 patch"),
        ("\U000f01ad", "nf-md-school - NERD FONTS v3 ONLY, blank on a v2 patch"),
    ],
    "status bar — scope": [("", "fa-search"), ("", "fa-circle-o, the whole set")],
    "status bar — library": [("", "fa-book"), ("", "fa-folder")],
    "status bar — documents": [("", "fa-file-text"), ("", "fa-files-o")],
    "status bar — pending chord": [("", "fa-keyboard-o")],
    "info pane — abstract": [("", "fa-align-left")],
    "info pane — citations": [("", "fa-quote-left")],
    "file kind — pdf": [("", "fa-file-pdf-o")],
    "file kind — notes": [("", "fa-sticky-note-o"), ("", "fa-pencil")],
    "file kind — slides": [("", "fa-desktop"), ("", "fa-file-powerpoint-o")],
    "file kind — supplement": [("", "fa-paperclip")],
    "file kind — html": [("", "fa-globe"), ("", "fa-html5")],
    "list — doctor finding": [("", "fa-warning"), ("", "fa-exclamation-circle")],
    "list — several files": [("", "fa-files-o"), ("", "fa-file-o")],
    "list — has notes": [("", "fa-pencil")],
}


def line(glyph: str, label: str) -> str:
    """`[g]` is three cells if the font agrees with rich. When it is not, every
    column to the right of the bracket shifts and you can see it."""
    return f"  [{glyph}]  U+{ord(glyph):04X}  cell_len={cell_len(glyph)}  {label}"


def main() -> None:
    print(f"\nSHIPPED — ui.GLYPHS, your config has icons = {config.load().get('ui.icons')}")
    print("  the two brackets on a row must be the same width as each other\n")
    for name, (ascii_, nerd) in ui.GLYPHS.items():
        same = "  (same in both columns on purpose)" if ascii_ == nerd else ""
        print(f"  {name:<20} ascii [{ascii_}]   nerd [{nerd}]  U+{ord(nerd):04X}{same}")

    print("\n\nCANDIDATES — nothing below is wired up yet\n")
    for slot, options in CANDIDATES.items():
        print(f"{slot}")
        for glyph, label in options:
            print(line(glyph, label))
        print()

    print("Pick per slot, then tell me the codepoints and I will wire them in.")
    print("A glyph that shows as a box, a blank, or shifts its bracket is a no.\n")


if __name__ == "__main__":
    main()
