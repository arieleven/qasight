from __future__ import annotations

from qasight.reporting.pyramid_viz import (
    PyramidModel,
    PyramidShape,
    classify_shape,
    render_pyramid_lines,
)


def test_classify_shape_healthy() -> None:
    shape, message = classify_shape(PyramidModel(unit=90, integration=20, e2e=5))
    assert shape is PyramidShape.HEALTHY
    assert "healthy" in message.lower()


def test_classify_shape_top_heavy_when_e2e_exceeds_unit() -> None:
    shape, _ = classify_shape(PyramidModel(unit=5, integration=10, e2e=20))
    assert shape is PyramidShape.TOP_HEAVY


def test_classify_shape_mid_bulge_when_integration_dominates() -> None:
    shape, _ = classify_shape(PyramidModel(unit=10, integration=50, e2e=5))
    assert shape is PyramidShape.MID_BULGE


def test_classify_shape_irregular_when_empty() -> None:
    shape, message = classify_shape(PyramidModel(unit=0, integration=0, e2e=0))
    assert shape is PyramidShape.IRREGULAR
    assert "no tiered tests" in message.lower()


def test_classify_shape_irregular_when_flat() -> None:
    shape, _ = classify_shape(PyramidModel(unit=10, integration=10, e2e=10))
    assert shape is PyramidShape.IRREGULAR


def test_render_pyramid_lines_no_data() -> None:
    assert render_pyramid_lines(PyramidModel(unit=0, integration=0, e2e=0)) == ["(no tier data)"]


def test_render_pyramid_lines_healthy_produces_three_tiers_plus_caption() -> None:
    lines = render_pyramid_lines(PyramidModel(unit=90, integration=20, e2e=5), width=33)
    assert len(lines) == 4
    assert all(len(line) == 33 for line in lines)
    assert "classic pyramid" in lines[-1]


def test_unit_only_and_no_e2e_are_not_called_irregular() -> None:
    from qasight.reporting.pyramid_viz import PyramidModel, PyramidShape, classify_shape

    assert classify_shape(PyramidModel(unit=4, integration=0, e2e=0))[0] is PyramidShape.HEALTHY
    assert classify_shape(PyramidModel(unit=10, integration=5, e2e=0))[0] is PyramidShape.HEALTHY
    assert classify_shape(PyramidModel(unit=5, integration=5, e2e=1))[0] is PyramidShape.IRREGULAR
