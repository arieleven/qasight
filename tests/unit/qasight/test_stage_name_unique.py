"""Stage names in a cycle are unique, ignoring case (each gets its own artifacts folder)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _write(tmp_path: Path, first: str, second: str) -> Path:
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        "cycles:\n  c:\n    stages:\n"
        f"      - {{name: {first}, equipment: command, args: [true]}}\n"
        f"      - {{name: {second}, equipment: command, args: [true]}}\n",
        encoding="utf-8",
    )
    return cfg


@pytest.mark.parametrize(("first", "second"), [("unit", "unit"), ("Unit", "unit")])
def test_duplicate_stage_names_are_refused(tmp_path: Path, first: str, second: str) -> None:
    with pytest.raises(ConfigValidationError, match="unique"):
        load_config(_write(tmp_path, first, second))


def test_distinct_stage_names_load(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, "unit", "api"))
    assert [s.name for s in config.cycles["c"].stages] == ["unit", "api"]
