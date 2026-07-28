"""Clipboard. Local tool if there is one, OSC52 through the terminal otherwise."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

TOOLS = {
    "wl-copy": ["wl-copy"],
    "xclip": ["xclip", "-selection", "clipboard"],
    "pbcopy": ["pbcopy"],
}


def copy(app: Any, text: str) -> str:
    """Copy `text`, returning the method used so the caller can say so."""
    choice = app.cfg.get("export.clipboard", "auto")
    candidates = [choice] if choice in TOOLS else ([] if choice == "osc52" else list(TOOLS))
    for name in candidates:
        if shutil.which(name):
            subprocess.run(TOOLS[name], input=text.encode(), check=False)
            return name
    app.copy_to_clipboard(text)  # OSC52: the only thing that works over SSH
    return "osc52"
