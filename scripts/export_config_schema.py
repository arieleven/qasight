"""Write the ``qasight.yaml`` JSON Schema to ``schema/qasight.schema.json``.

A copy goes to ``schema/testosterone.schema.json``, the name before 1.6.

Editors load the committed file (by URL or a relative ``$schema`` modeline) to
autocomplete and validate configs. It is generated from
:mod:`qasight.config.json_schema`; CI re-runs this script and fails if the
committed file is stale.

Usage::

    python scripts/export_config_schema.py            # write the file
    python scripts/export_config_schema.py --check    # exit 1 if it is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schema" / "qasight.schema.json"
# The same schema under its name from before the rename to QASight (1.6), so configs
# whose modeline still points at it keep validating.
LEGACY_SCHEMA_PATH = REPO_ROOT / "schema" / "testosterone.schema.json"
SCHEMA_PATHS = (SCHEMA_PATH, LEGACY_SCHEMA_PATH)


def render_schema() -> str:
    from qasight.config.json_schema import build_json_schema

    return json.dumps(build_json_schema(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the qasight.yaml JSON Schema.")
    parser.add_argument(
        "--check", action="store_true", help="fail if the committed schema is stale"
    )
    args = parser.parse_args(argv)

    rendered = render_schema()
    if args.check:
        stale = 0
        for path in SCHEMA_PATHS:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != rendered:
                print(
                    f"{path.relative_to(REPO_ROOT)} is out of date. Run "
                    "`python scripts/export_config_schema.py` and commit the result.",
                    file=sys.stderr,
                )
                stale = 1
        return stale

    for path in SCHEMA_PATHS:
        path.write_text(rendered, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
