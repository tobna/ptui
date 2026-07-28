"""`SelectList` — the one modal picker, shared by every "choose one of these".

Built once and reused by the sort picker, `files.open_pick` and `lib.switch`
(and, later, saved searches and doctor findings).

Deviation from SPEC, deliberate: navigation is `up`/`down` (plus `ctrl+n` /
`ctrl+p`), not `j`/`k`. The filter box has focus so that fuzzy filtering happens
as you type, and a key cannot be both a letter and a motion.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from ptui import keymap, library


@dataclass(frozen=True, slots=True)
class Item:
    label: str
    value: Any
    hint: str = ""
    """Right-hand column: the sort key, the bound keys, a path — whatever teaches."""
    haystack: str = field(default="")

    def matches(self, needle: str) -> bool:
        return not needle or library.is_subsequence(
            needle.replace(" ", ""), f"{self.label} {self.hint} {self.haystack}".casefold()
        )


class SelectList(ModalScreen[tuple[Any, bool] | None]):
    """Dismisses with `(value, inverted)`, or None when cancelled."""

    DEFAULT_CSS = """
    SelectList { align: center middle; }
    SelectList > Vertical {
        width: 70%; max-width: 100; height: auto; max-height: 80%;
        border: round $accent; background: $surface;
    }
    SelectList #picker-title { padding: 0 1; text-style: bold; }
    SelectList Input { border: none; padding: 0 1; }
    SelectList OptionList { height: auto; max-height: 20; border: none; }
    """

    def __init__(self, items: list[Item], *, title: str, current: Any = None) -> None:
        super().__init__()
        self.items = items
        self.title_text = title
        self.current = current
        self.shown: list[Item] = list(items)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.title_text, id="picker-title")
            yield Input(placeholder="filter")
            yield OptionList()

    def on_mount(self) -> None:
        self._populate()
        index = next((i for i, it in enumerate(self.shown) if it.value == self.current), 0)
        self.query_one(OptionList).highlighted = index
        self.query_one(Input).focus()

    def _populate(self) -> None:
        options = self.query_one(OptionList)
        options.clear_options()
        for item in self.shown:
            marker = ">" if item.value == self.current else " "
            hint = f"  [dim]{item.hint}[/]" if item.hint else ""
            options.add_option(f"{marker} {item.label}{hint}")
        options.highlighted = 0 if self.shown else None

    def on_input_changed(self, event: Input.Changed) -> None:
        needle = event.value.casefold()
        self.shown = [item for item in self.items if item.matches(needle)]
        self._populate()

    def on_key(self, event: Any) -> None:
        """Dispatch through `[modes.picker]` — the same table as everything else."""
        token = keymap.normalize(event.key, event.character)
        binding = self.app.km.lookup("picker", (token,))
        if binding is None:
            return  # anything else is filter text
        event.stop()

        options = self.query_one(OptionList)
        if binding.cmd in ("nav.down", "nav.up"):
            step = binding.args.get("count", 1) * (1 if binding.cmd == "nav.down" else -1)
            if options.option_count:
                options.highlighted = max(
                    0, min(options.option_count - 1, (options.highlighted or 0) + step)
                )
        elif binding.cmd == "picker.confirm":
            index = options.highlighted
            picked = self.shown[index] if index is not None and self.shown else None
            self.dismiss((picked.value, binding.args.get("invert", False)) if picked else None)
        elif binding.cmd == "picker.cancel":
            self.dismiss(None)


def pick(app: Any, items: list[Item], *, title: str, current: Any = None) -> Callable[..., Any]:
    """`app.push_screen(SelectList(...), on_confirm)`, minus the ceremony.

    Usage: `ui.pick(app, items, title="Sort by")(lambda value, inverted: ...)`.
    """

    def run(on_confirm: Callable[[Any, bool], None]) -> None:
        def handle(result: tuple[Any, bool] | None) -> None:
            if result is not None:
                on_confirm(*result)

        app.push_screen(SelectList(items, title=title, current=current), handle)

    return run
