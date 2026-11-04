#!/usr/bin/env python3
"""Export the static demo site this repository publishes on Pages.

The export itself lives in ``qasight_api/static_site.py`` (it is also behind
``qasight export-site``); this script is the demo's entry point. The demo builds
the UI with ``VITE_STATIC_DATA_BASE`` itself, so here only the data is written,
marked as the demo (fake-api fails on purpose), optionally with made-up
resource pools so the Resources page has content.

Usage::

    python scripts/export_static_site.py --out public --runs 5 \\
        --site-url "$CI_PAGES_URL" --demo-resources
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from qasight_api.static_site import (  # noqa: E402
    DEFAULT_DEMO_AI_MODEL,
    Exporter,
    _cache_run_cases,
    _ran_cycles_only,
    _scrub,
)

__all__ = [
    "DEFAULT_DEMO_AI_MODEL",
    "Exporter",
    "_cache_run_cases",
    "_ran_cycles_only",
    "_scrub",
    "main",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", default="public", help="Output directory for the static site (default: public)."
    )
    parser.add_argument(
        "--runs", type=int, default=10, help="How many recent runs to export (default: 10)."
    )
    parser.add_argument(
        "--delta-pairs",
        type=int,
        default=20,
        help="Maximum run comparisons to export (default: 20).",
    )
    parser.add_argument(
        "--site-url",
        default=os.getenv("CI_PAGES_URL", ""),
        help="Public base URL of the published site; used to absolutize report links.",
    )
    parser.add_argument(
        "--demo-resources",
        action="store_true",
        help="Fill the history database with made-up resource pools first (qasight lease demo), "
        "so the Resources page shows something. Only for a throwaway demo database.",
    )
    args = parser.parse_args(argv)
    # CI pipes stdout; without this a slow or cancelled export shows no progress at all.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]

    out_dir = Path(args.out).expanduser().resolve()
    exporter = Exporter(
        out_dir=out_dir,
        site_url=args.site_url,
        run_limit=max(1, args.runs),
        delta_pairs=max(0, args.delta_pairs),
        demo_resources=args.demo_resources,
        demo=True,
        scrub_root=REPO_ROOT,
    )
    return exporter.run()


if __name__ == "__main__":
    raise SystemExit(main())
