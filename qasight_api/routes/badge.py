"""README badges for a cycle's latest run: ``/api/v1/badge.svg`` and ``/api/v1/badge.json``.

Reads like every other GET: open, or behind ``TESTO_PROTECT_READS``, in which
case a public README can't show it (put the badge ``qasight badge`` writes in CI
on a public page instead).
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from qasight.services.badge import badge_for_cycle, render_svg, shields_endpoint

router = APIRouter(prefix="/api/v1", tags=["badge"])

# Short, so a README shows a new run within minutes; camo (GitHub's image proxy) honours it.
_CACHE = "max-age=300"


@router.get("/badge.svg", response_class=Response)
def get_badge_svg(
    cycle: str | None = Query(default=None, max_length=200),
    label: str = Query(default="tests", max_length=40),
) -> Response:
    """The latest run of ``cycle`` (any cycle when omitted) as an SVG badge."""
    svg = render_svg(badge_for_cycle(cycle.strip() if cycle else None, label=label))
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": _CACHE})


@router.get("/badge.json")
def get_badge_json(
    cycle: str | None = Query(default=None, max_length=200),
    label: str = Query(default="tests", max_length=40),
) -> dict[str, object]:
    """The same badge as a shields.io endpoint (``img.shields.io/endpoint?url=...``)."""
    return shields_endpoint(badge_for_cycle(cycle.strip() if cycle else None, label=label))
