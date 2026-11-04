"""Write the FastAPI OpenAPI schema to ``frontend/openapi.json``.

The React app's API types are generated from this file
(``npm --prefix frontend run gen:api``), so the backend's Pydantic models are
the single source of truth for the HTTP contract. CI re-runs this script and
fails if the committed schema is stale.

Usage::

    python scripts/export_openapi.py            # write the file
    python scripts/export_openapi.py --check    # exit 1 if it is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "frontend" / "openapi.json"


def render_schema() -> str:
    from qasight_api.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI schema.")
    parser.add_argument(
        "--check", action="store_true", help="fail if the committed schema is stale"
    )
    args = parser.parse_args(argv)

    rendered = render_schema()
    if args.check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if current != rendered:
            print(
                f"{SCHEMA_PATH.relative_to(REPO_ROOT)} is out of date. Run "
                "`python scripts/export_openapi.py && npm --prefix frontend run gen:api` and commit the result.",
                file=sys.stderr,
            )
            return 1
        return 0

    SCHEMA_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {SCHEMA_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
