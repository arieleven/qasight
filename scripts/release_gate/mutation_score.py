"""Release gate: fail when too few of mutmut's mutants were caught by the tests.

Reads the ``mutants/**/*.meta`` files ``mutmut run`` leaves (``[tool.mutmut]`` in
pyproject.toml says what is mutated) and prints the score. Exit code 0 when the
share of killed mutants is at least ``--min`` percent, 1 otherwise.

    python scripts/release_gate/mutation_score.py --min 85
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# mutmut's exit codes: 0 the tests still passed (survived), 33 no test covers it,
# 34 skipped; anything else (1 failed, 36 timeout, -11 crash, ...) killed it.
SURVIVED = 0
NOT_COUNTED = {33, 34}


def score(root: Path) -> tuple[int, int, int]:
    """(killed, survived, not counted) over every mutant under ``root``."""
    codes: Counter[int] = Counter()
    for meta in root.glob("**/*.meta"):
        for code in json.loads(meta.read_text(encoding="utf-8"))["exit_code_by_key"].values():
            if code is not None:
                codes[int(code)] += 1
    survived = codes[SURVIVED]
    skipped = sum(codes[c] for c in NOT_COUNTED)
    killed = sum(codes.values()) - survived - skipped
    return killed, survived, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min", type=float, required=True, help="minimum score, percent")
    parser.add_argument("--mutants", type=Path, default=Path("mutants"))
    args = parser.parse_args()
    killed, survived, skipped = score(args.mutants)
    if killed + survived == 0:
        print("::error::no mutants were tested; did `mutmut run` run?")
        return 1
    pct = 100 * killed / (killed + survived)
    print(
        f"mutation score {pct:.1f}% ({killed} killed, {survived} survived, {skipped} not covered)"
    )
    if pct < args.min:
        print(
            f"::error::mutation score {pct:.1f}% is below {args.min:g}%; `mutmut results` lists the survivors"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
