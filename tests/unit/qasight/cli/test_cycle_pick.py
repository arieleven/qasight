"""One rule picks the cycle when --cycle is left out (qasight/cli/cycle_pick.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from qasight.cli import cycle_pick
from qasight.cli.cycle_pick import pick_cycle, require_cycle

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in cycle_pick.CYCLE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_CYCLE", "env")
    assert pick_cycle(" given ") == cycle_pick.PickedCycle("given", "")


def test_env_then_only_config_cycle_then_newest_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cycle_pick, "_config_cycles", lambda config: ["only"])
    monkeypatch.setattr(cycle_pick, "_newest_recorded_cycle", lambda: "newest")
    monkeypatch.setenv("QASIGHT_CYCLE", "from-env")
    assert pick_cycle(None).name == "from-env"
    monkeypatch.delenv("QASIGHT_CYCLE")
    assert pick_cycle(None) == cycle_pick.PickedCycle("only", "only cycle in qasight.yaml")
    monkeypatch.setattr(cycle_pick, "_config_cycles", lambda config: ["a", "b"])
    assert pick_cycle(None) == cycle_pick.PickedCycle("newest", "newest recorded run")


def test_nothing_to_pick_exits_2_and_lists_cycles(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cycle_pick, "_config_cycles", lambda config: ["a", "b"])
    monkeypatch.setattr(cycle_pick, "_newest_recorded_cycle", lambda: None)
    with pytest.raises(typer.Exit) as exc:
        require_cycle(None, config=Path("x.yaml"))
    assert exc.value.exit_code == 2
    assert "a, b" in capsys.readouterr().err


def test_a_guess_is_said_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cycle_pick, "_config_cycles", lambda config: ["solo"])
    assert require_cycle(None) == "solo"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cycle: solo (only cycle in qasight.yaml)" in captured.err
