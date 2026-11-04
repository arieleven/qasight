"""`qasight run` without --cycle on a config with several cycles says to pick one."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.errors import PlanNotFoundError
from qasight.config.loader import load_config
from qasight.config.resolver import resolve_plan

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def test_missing_cycle_with_several_cycles_says_to_pick_one(tmp_path: Path) -> None:
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        "cycles:\n"
        "  a: {stages: [{name: s, equipment: command, args: [true]}]}\n"
        "  b: {stages: [{name: s, equipment: command, args: [true]}]}\n",
        encoding="utf-8",
    )
    with pytest.raises(PlanNotFoundError) as info:
        resolve_plan(load_config(cfg), plan_name=None)
    message = str(info.value)
    assert "pick one with --cycle" in message
    assert "<unspecified>" not in message
    assert "a, b" in message
