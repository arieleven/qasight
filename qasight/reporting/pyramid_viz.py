"""ASCII test-pyramid visualizer from tier counts (no Rich / CLI imports)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PyramidShape(str, Enum):
    HEALTHY = "healthy"
    TOP_HEAVY = "top_heavy"
    MID_BULGE = "mid_bulge"
    IRREGULAR = "irregular"


@dataclass(frozen=True)
class PyramidModel:
    unit: int
    integration: int
    e2e: int


def classify_shape(m: PyramidModel) -> tuple[PyramidShape, str]:
    """Return shape enum and a short human status line."""

    u, i, e = m.unit, m.integration, m.e2e
    total = u + i + e
    if total == 0:
        return PyramidShape.IRREGULAR, "No tiered tests yet: set `tier:` on a stage."

    if e > u:
        return PyramidShape.TOP_HEAVY, "More end-to-end tests than unit tests: slow and brittle."
    if i > u and i > e:
        return PyramidShape.MID_BULGE, "More integration tests than unit or end-to-end tests."
    if u == total:
        return (
            PyramidShape.HEALTHY,
            "Every test counts as a unit test. Set `tier:` on a stage to sort integration and end-to-end tests.",
        )
    if u > i >= e:
        return PyramidShape.HEALTHY, "Mostly unit tests, fewest end-to-end: a healthy pyramid."
    if u == i == e:
        return PyramidShape.IRREGULAR, "As many end-to-end tests as unit tests."
    return (
        PyramidShape.IRREGULAR,
        "Not a clean pyramid: each tier should be smaller than the one below it.",
    )


def _allocate_widths(total_width: int, u: int, i: int, e: int) -> tuple[int, int, int]:
    """Proportional inner widths for E2E, Integration, Unit layers (inside slashes)."""

    t = u + i + e
    if t <= 0 or total_width < 9:
        return (3, 3, 3)

    raw_e = max(1, round(total_width * e / t))
    raw_i = max(1, round(total_width * i / t))
    raw_u = max(1, round(total_width * u / t))

    # Normalize to exact total_width (odd inner widths look best for symmetry)
    inner = [raw_e, raw_i, raw_u]
    s = sum(inner)
    idx = 0
    while s != total_width and s > 0:
        if s > total_width:
            k = max(range(3), key=lambda j: inner[j])
            if inner[k] > 1:
                inner[k] -= 1
                s -= 1
            else:
                break
        else:
            inner[idx % 3] += 1
            s += 1
        idx += 1
    return (inner[0], inner[1], inner[2])


def _line(inner_w: int) -> str:
    """One pyramid tier: /---\\ with inner_w dashes between slashes."""

    inner_w = max(1, inner_w)
    return "/" + "-" * inner_w + "\\"


def _center_line(line: str, width: int) -> str:
    if len(line) >= width:
        return line[:width]
    pad = width - len(line)
    left = pad // 2
    return " " * left + line + " " * (pad - left)


def render_pyramid_lines(m: PyramidModel, *, width: int = 33) -> list[str]:
    """ASCII lines for the three-tier pyramid (behavior depends on classify_shape)."""

    shape, _ = classify_shape(m)
    u, i, e = m.unit, m.integration, m.e2e
    t = u + i + e
    if t <= 0:
        return ["(no tier data)"]

    # Inner width budget: leave margin for centering in `width`
    max_inner = max(3, width - 4)
    w_e, w_i, w_u = _allocate_widths(max_inner, u, i, e)

    if shape == PyramidShape.TOP_HEAVY:
        # Inverted cone: wide top (E2E), narrow bottom (Unit)
        lines = [_line(w_u), _line(w_i), _line(w_e)]
        lines.append(r"\/  ice-cream cone (E2E-heavy)")
    elif shape == PyramidShape.MID_BULGE:
        # Diamond-ish: narrow, wide middle, narrow
        mid = min(max_inner, max(w_e, w_i, w_u, min(max_inner, max(w_e, w_i, w_u) + 4)))
        top = max(1, min(w_e, max(1, mid - 4)))
        bot = max(1, min(w_u, max(1, mid - 4)))
        lines = [_line(top), _line(mid), _line(bot)]
        lines.append("◇  integration bulge")
    else:
        # Healthy or default: classic pyramid narrow → wide (E2E top, Unit bottom)
        order = (w_e, w_i, w_u)
        lines = [_line(order[0]), _line(order[1]), _line(order[2])]
        if shape == PyramidShape.HEALTHY:
            lines.append("/\\  classic pyramid")
        else:
            lines.append("/\\  tier mix")

    return [_center_line(s, width) for s in lines]
