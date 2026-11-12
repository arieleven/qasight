"""Write ``integrations/github-action/action.yml`` from the root ``action.yml``.

The root file is the Marketplace action (``uses: arieleven/qasight@v1``); the
subfolder keeps the older path (``.../integrations/github-action@v1``) working.
They are one action: edit ``action.yml`` and run this. ``--check`` exits 1 when
the copy is stale (CI runs it through tests/contract/ci/test_action_copies.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "action.yml"
COPY = ROOT / "integrations" / "github-action" / "action.yml"
SCRIPT = "integrations/github-action/run_testo_action.py"
HEADER = (
    "# Generated from the root action.yml by scripts/sync_action_copy.py; edit that file.\n"
    "# This path (.../integrations/github-action@v1) is kept working for older workflows.\n"
)


def render(source: str) -> str:
    """The subfolder copy: the root action without its comment header, reaching the script
    next to it instead of in the subfolder."""
    lines = source.splitlines(keepends=True)
    while lines and lines[0].startswith("#"):
        lines.pop(0)
    body = "".join(lines).replace(
        f"$GITHUB_ACTION_PATH/{SCRIPT}", "$GITHUB_ACTION_PATH/run_testo_action.py"
    )
    return HEADER + body


def main(argv: list[str]) -> int:
    expected = render(SOURCE.read_text(encoding="utf-8"))
    if "--check" in argv:
        if COPY.read_text(encoding="utf-8") != expected:
            print(f"{COPY.relative_to(ROOT)} is stale: run python scripts/sync_action_copy.py")
            return 1
        return 0
    COPY.write_text(expected, encoding="utf-8")
    print(f"wrote {COPY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
