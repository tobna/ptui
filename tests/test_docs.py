import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_keys_md_is_current():
    """`KEYS.md` is generated. Regenerate it in the commit that changes a binding."""
    import keydoc

    assert (ROOT / "KEYS.md").read_text() == keydoc.render(), "KEYS.md is stale — run `uv run python scripts/keydoc.py`"
