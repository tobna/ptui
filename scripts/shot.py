"""Drive the TUI headlessly and save a picture of the screen.

This is how an agent (or you, over SSH) *looks* at ptui without a terminal:
Textual's Pilot presses real keys, `export_screenshot` writes an SVG of the
final frame, and `rsvg-convert` turns it into a PNG that can be opened.

    uv run python scripts/shot.py /tmp/shot.png j j space slash n a u
    uv run python scripts/shot.py /tmp/shot.png --size 200x50 g

Key names are Textual's (`space`, `escape`, `ctrl+d`, `enter`); `slash` and
friends work for punctuation. Nothing here is part of the app.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from ptui import config, keymap
from ptui.app import PtuiApp

CONVERTERS = (
    ["rsvg-convert", "-w", "1600", "{svg}", "-o", "{png}"],
    ["magick", "-density", "150", "{svg}", "{png}"],
    ["inkscape", "{svg}", "-o", "{png}"],
)


async def shoot(keys: list[str], out: Path, size: tuple[int, int], settle: float) -> None:
    app = PtuiApp(config.load(), keymap.load())
    async with app.run_test(size=size) as pilot:
        await pilot.pause(settle)
        for key in keys:
            await pilot.press(key)
            await pilot.pause(settle)
        out.with_suffix(".svg").write_text(app.export_screenshot())


def to_png(svg: Path, png: Path) -> None:
    for template in CONVERTERS:
        if shutil.which(template[0]):
            subprocess.run(
                [part.format(svg=svg, png=png) for part in template],
                check=True,
                capture_output=True,
            )
            return
    print(f"no SVG converter found; the SVG is at {svg}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="PNG to write (the SVG lands beside it)")
    parser.add_argument("keys", nargs="*", help="keys to press, in order")
    parser.add_argument("--size", default="140x40", help="terminal size, WxH")
    parser.add_argument("--settle", type=float, default=0.2, help="pause after each key")
    args = parser.parse_args()

    width, _, height = args.size.partition("x")
    asyncio.run(shoot(args.keys, args.out, (int(width), int(height)), args.settle))
    to_png(args.out.with_suffix(".svg"), args.out)
    print(args.out)


if __name__ == "__main__":
    main()
