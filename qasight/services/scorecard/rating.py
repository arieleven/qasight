"""Category scores from findings, the way SonarQube and Codacy rate a codebase.

Two rules, both from those tools rather than invented here:

- **Security and supply chain** are rated by the **worst** open finding, like
  SonarQube's security rating: none is A, only low is B, medium C, high D,
  critical F. One critical issue is a release problem however big the code is.
- **Code quality** is rated by **density**: severity points per 1,000 lines of
  source code, like Codacy's issue density and SonarQube's debt ratio. Ten
  style warnings in a million-line repository are not ten in a script.

A category with nothing measured has no score and is left out of the overall
score (it is not counted as zero), so a repository without tests isn't graded
on coverage it never reported.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

# Weight of one finding in the density, by severity (SonarQube's remediation
# effort is roughly in these ratios: blocker 10x a minor issue).
DENSITY_WEIGHTS: dict[str, float] = {
    "critical": 10.0,
    "high": 5.0,
    "medium": 2.0,
    "low": 0.5,
    "info": 0.1,
}

# Weighted findings per 1,000 source lines -> score, joined by straight lines.
# Up to 2 per KLOC is an A (90+), 5 a B (80), 10 a C (70), 25 a D (60), past that F
# (the grades are QASight's usual 90/80/70/60).
_DENSITY_CURVE: tuple[tuple[float, float], ...] = (
    (0.0, 100.0),
    (2.0, 90.0),
    (5.0, 80.0),
    (10.0, 70.0),
    (25.0, 60.0),
    (100.0, 30.0),
)

# Worst severity -> its band (top, bottom): low is a B, medium a C, high a D, critical an F.
_WORST_BANDS: dict[str, tuple[float, float]] = {
    "low": (89.0, 80.0),
    "medium": (79.0, 70.0),
    "high": (69.0, 60.0),
    "critical": (59.0, 20.0),
}

# Categories rated by their worst finding; the rest that have findings go by density.
WORST_FINDING_CATEGORIES = frozenset({"security", "supply-chain"})
# A repository this small is treated as this many lines (a 40-line script with one warning isn't an F).
MIN_LINES = 1000


def density(counts: Mapping[str, int], source_lines: int) -> float:
    """Weighted findings per 1,000 source lines."""
    points = sum(DENSITY_WEIGHTS.get(sev, 0.0) * n for sev, n in counts.items())
    return points * 1000.0 / max(source_lines, MIN_LINES)


def _half_up(value: float) -> int:
    """Round .5 up, like the UI's Math.round."""
    return math.floor(value + 0.5)


def density_score(counts: Mapping[str, int], source_lines: int) -> int:
    value = density(counts, source_lines)
    for (x0, y0), (x1, y1) in zip(_DENSITY_CURVE, _DENSITY_CURVE[1:], strict=False):
        if value <= x1:
            return _half_up(y0 + (y1 - y0) * (value - x0) / (x1 - x0))
    # Past the last point: keep falling, never below 0.
    last_x, last_y = _DENSITY_CURVE[-1]
    return max(0, _half_up(last_y * last_x / value))


def worst_finding_score(counts: Mapping[str, int]) -> int:
    """100 with no findings; else the worst severity's band, lower in it the more of them."""
    for severity in ("critical", "high", "medium", "low"):
        n = counts.get(severity, 0)
        if n:
            top, bottom = _WORST_BANDS[severity]
            # The first one puts the score at the top of the band; every doubling costs 2 points.
            return _half_up(max(bottom, top - 2.0 * math.log2(n)))
    return 100


def findings_score(category: str, counts: Mapping[str, int], source_lines: int) -> int:
    if category in WORST_FINDING_CATEGORIES:
        return worst_finding_score(counts)
    return density_score(counts, source_lines)
