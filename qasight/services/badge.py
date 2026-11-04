"""A README badge for a cycle's latest run: ``tests | 412 passed · 2 flaky``.

Rendered two ways from the same :class:`Badge`:

- :func:`render_svg`, a flat badge in the shields.io style, for ``qasight badge``
  (CI writes it next to a published report) and ``GET /api/v1/badge.svg``;
- :func:`shields_endpoint`, the JSON a `shields.io endpoint badge
  <https://shields.io/badges/endpoint-badge>`_ reads, for ``GET /api/v1/badge.json``.

The message counts the run's tests (failed and broken together), and the
cycle's flaky tests over its recent runs when there are any.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from qasight.history.views import CompletedRunView

# shields.io's named colours, so both renderings look the same.
GREEN = "#4c1"
RED = "#e05d44"
YELLOW = "#dfb317"
GREY = "#9f9f9f"
_SHIELDS_COLOR = {GREEN: "brightgreen", RED: "red", YELLOW: "yellow", GREY: "lightgrey"}


@dataclass(frozen=True)
class Badge:
    label: str
    message: str
    color: str


def latest_run(cycle: str | None) -> CompletedRunView | None:
    """The newest recorded run of ``cycle`` (any cycle when ``None``)."""
    from qasight.history.read_model import latest_run_per_cycle, list_recent_runs

    if cycle:
        return latest_run_per_cycle().get(cycle)
    runs = list_recent_runs(limit=1)
    return runs[0] if runs else None


def badge_for_run(run: CompletedRunView | None, *, flaky: int = 0, label: str = "tests") -> Badge:
    """The badge for ``run``; grey "no runs" when there is none or it recorded no tests."""
    if run is None or not run.total_tests:
        return Badge(label, "no runs" if run is None else "no tests", GREY)
    failed = (run.failed or 0) + (run.broken or 0)
    parts = []
    if failed:
        parts.append(f"{failed} failed")
    parts.append(f"{run.passed or 0} passed")
    if flaky:
        parts.append(f"{flaky} flaky")
    color = RED if failed else YELLOW if flaky else GREEN
    return Badge(label, " · ".join(parts), color)


def badge_for_cycle(cycle: str | None, *, label: str = "tests") -> Badge:
    """:func:`badge_for_run` for the latest run of ``cycle``, with that cycle's flaky count."""
    from qasight.services.flaky_service import find_flaky_tests

    run = latest_run(cycle)
    flaky = len(find_flaky_tests(cycle=run.cycle).flaky) if run and run.cycle else 0
    return badge_for_run(run, flaky=flaky, label=label)


def _text_width(text: str) -> int:
    """Rough width of ``text`` in 11px Verdana: wide enough, never clipped."""
    wide = sum(1 for ch in text if ch.isupper() or ch in "mwMW%")
    return int(len(text) * 6.5 + wide * 1.5) + 10


def render_svg(badge: Badge) -> str:
    """A flat SVG badge, with an accessible name (``<title>``) of ``label: message``."""
    left, right = _text_width(badge.label), _text_width(badge.message)
    total = left + right
    label, message = escape(badge.label), escape(badge.message)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" '
        f'aria-label="{label}: {message}">'
        f"<title>{label}: {message}</title>"
        '<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" '
        'stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>'
        '<g clip-path="url(#r)">'
        f'<rect width="{left}" height="20" fill="#555"/>'
        f'<rect x="{left}" width="{right}" height="20" fill="{badge.color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/></g>'
        '<g fill="#fff" text-anchor="middle" '
        'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{left / 2}" y="15" fill="#010101" fill-opacity=".3">{label}</text>'
        f'<text x="{left / 2}" y="14">{label}</text>'
        f'<text x="{left + right / 2}" y="15" fill="#010101" fill-opacity=".3">{message}</text>'
        f'<text x="{left + right / 2}" y="14">{message}</text></g></svg>'
    )


def shields_endpoint(badge: Badge) -> dict[str, object]:
    """The JSON a shields.io endpoint badge reads (``schemaVersion`` 1)."""
    return {
        "schemaVersion": 1,
        "label": badge.label,
        "message": badge.message,
        "color": _SHIELDS_COLOR.get(badge.color, badge.color.lstrip("#")),
    }
