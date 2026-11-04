"""Lightweight tests for ``qasight.reporting.metrics_extractor`` helpers."""

from __future__ import annotations

import json
from pathlib import Path

from qasight.reporting.metrics_extractor import extract_best


def test_extract_best_from_results_dir(tmp_path: Path) -> None:
    p = tmp_path / "abc-result.json"
    p.write_text(
        json.dumps(
            {
                "status": "passed",
                "start": 0,
                "stop": 100,
            }
        ),
        encoding="utf-8",
    )
    em = extract_best(results_dir=tmp_path)
    assert em is not None
    assert em.total_tests >= 1
