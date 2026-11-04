"""``max_parallel_stages`` in ``qasight.yaml``: parsed, validated and in the JSON schema."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.json_schema import build_json_schema
from qasight.config.loader import load_config

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _config(tmp_path: Path, cycle_extra: str, stages: str = "[unit, api]") -> Path:
    names = [n.strip() for n in stages.strip("[]").split(",")]
    stage_lines = "".join(f"      - {{name: {n}, framework: pytest}}\n" for n in names)
    path = tmp_path / "qasight.yaml"
    path.write_text(
        f"version: 1\ncycles:\n  app:\n{cycle_extra}    stages:\n{stage_lines}",
        encoding="utf-8",
    )
    return path


def test_defaults_to_one_stage_at_a_time(tmp_path: Path) -> None:
    assert load_config(_config(tmp_path, "")).cycles["app"].max_parallel_stages == 1


def test_reads_the_cycle_setting(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "    max_parallel_stages: 3\n")
    assert load_config(cfg).cycles["app"].max_parallel_stages == 3


@pytest.mark.parametrize("value", ["0", "-2", "two", "1.5", "true"])
def test_rejects_values_that_are_not_a_positive_whole_number(tmp_path: Path, value: str) -> None:
    cfg = _config(tmp_path, f"    max_parallel_stages: {value}\n")
    with pytest.raises(ConfigValidationError, match="whole number of at least 1"):
        load_config(cfg)


def test_parallel_cycles_need_unique_stage_names(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "    max_parallel_stages: 2\n", stages="[unit, unit, api]")
    with pytest.raises(ConfigValidationError, match="unique"):
        load_config(cfg)
    # Sequential cycles too: the second stage would wipe the first one's results.
    cfg = _config(tmp_path, "", stages="[unit, unit]")
    with pytest.raises(ConfigValidationError, match="unique"):
        load_config(cfg)


def test_schema_documents_the_setting() -> None:
    prop = build_json_schema()["definitions"]["cycle"]["properties"]["max_parallel_stages"]
    assert prop["type"] == "integer"
    assert prop["minimum"] == 1
