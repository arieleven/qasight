"""History retention: which runs expire, and that rows, test rows and snapshots go together."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight import paths
from qasight.cli.app import app
from qasight.config.errors import ConfigValidationError
from qasight.config.history_section import HistoryRetention, parse_history
from qasight.history import retention
from qasight.repository.db import get_repository, get_test_result_store
from qasight.repository.models import RunStatus

pytestmark = pytest.mark.tier_fast

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _key(run_id: str, cycle: str | None, days_ago: float, status: RunStatus = RunStatus.COMPLETED):
    return (run_id, cycle, NOW - timedelta(days=days_ago), status)


# --- config -----------------------------------------------------------------


def test_history_section_defaults_to_keeping_everything() -> None:
    assert parse_history(None).retention == HistoryRetention()
    assert not parse_history({"retention": None}).retention.enabled
    assert not parse_history({}).retention.enabled


def test_history_section_reads_both_limits() -> None:
    got = parse_history({"retention": {"days": 90, "runs_per_cycle": 200}}).retention
    assert got == HistoryRetention(days=90, runs_per_cycle=200)
    assert got.enabled
    assert parse_history({"retention": {"days": 7}}).retention == HistoryRetention(days=7)


@pytest.mark.parametrize(
    "raw",
    [
        {"retention": {"days": 0}},
        {"retention": {"days": "90"}},
        {"retention": {"runs_per_cycle": True}},
        {"retention": {"weeks": 2}},
        {"keep": 3},
        {"retention": 90},
        "90 days",
    ],
)
def test_bad_history_sections_are_config_errors(raw: object) -> None:
    with pytest.raises(ConfigValidationError):
        parse_history(raw)


# --- selection ----------------------------------------------------------------


def test_nothing_expires_without_limits() -> None:
    assert retention.expired_run_ids([_key("a", "unit", 1000)], HistoryRetention(), now=NOW) == []


def test_days_limit_expires_old_runs_oldest_first() -> None:
    keys = [_key("new", "unit", 1), _key("old", "unit", 31), _key("older", "unit", 40)]
    got = retention.expired_run_ids(keys, HistoryRetention(days=30), now=NOW)
    assert got == ["older", "old"]


def test_runs_per_cycle_keeps_each_cycles_newest() -> None:
    keys = [
        _key("u3", "unit", 1),
        _key("n2", "nightly", 2),
        _key("u2", "unit", 3),
        _key("u1", "unit", 4),
        _key("n1", "nightly", 5),
        _key("x1", None, 6),
    ]
    got = retention.expired_run_ids(keys, HistoryRetention(runs_per_cycle=2), now=NOW)
    assert got == ["u1"]  # nightly has two, the cycle-less run is its own group


def test_a_run_goes_when_it_breaks_either_limit() -> None:
    keys = [_key("u3", "unit", 1), _key("u2", "unit", 2), _key("u1", "unit", 40)]
    got = retention.expired_run_ids(keys, HistoryRetention(days=30, runs_per_cycle=2), now=NOW)
    assert got == ["u1"]
    got = retention.expired_run_ids(keys, HistoryRetention(days=100, runs_per_cycle=1), now=NOW)
    assert got == ["u1", "u2"]


def test_running_runs_never_expire_and_do_not_count() -> None:
    keys = [
        _key("live", "unit", 0, RunStatus.RUNNING),
        _key("u2", "unit", 1),
        _key("u1", "unit", 50),
    ]
    got = retention.expired_run_ids(keys, HistoryRetention(days=30, runs_per_cycle=1), now=NOW)
    assert got == ["u1"]


def test_order_of_the_input_does_not_matter() -> None:
    keys = [_key("u1", "unit", 4), _key("u3", "unit", 1), _key("u2", "unit", 3)]
    got = retention.expired_run_ids(keys, HistoryRetention(runs_per_cycle=1), now=NOW)
    assert got == ["u1", "u2"]


# --- pruning ------------------------------------------------------------------


def _record_run(*, cycle: str, days_ago: int) -> str:
    """A completed run of ``cycle`` that started ``days_ago`` days before NOW; returns its id."""
    repo = get_repository()
    record = repo.create_run(
        status=RunStatus.COMPLETED, metadata={"plan": cycle, "health_pct": 100.0}
    )
    record.start_time = NOW - timedelta(days=days_ago)
    record.end_time = record.start_time + timedelta(minutes=1)
    repo.bulk_update([record])
    run_id = str(record.id)
    get_test_result_store().replace(run_id, {"t": {"name": "t", "status": "passed"}})
    return run_id


def test_prune_removes_rows_test_rows_and_snapshots_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "static" / "history")
    ids = {}
    for name, days in (("keep", 1), ("old", 45), ("older", 60)):
        ids[name] = _record_run(cycle="unit", days_ago=days)
        (tmp_path / "static" / "history" / ids[name] / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(retention, "datetime", _FrozenDatetime)

    dry = retention.prune_history(HistoryRetention(days=30), dry_run=True)
    assert dry.run_ids == (ids["older"], ids["old"])
    assert dry.runs_deleted == 0
    assert get_repository().get_run(ids["old"]) is not None

    report = retention.prune_history(HistoryRetention(days=30))

    assert (report.runs_deleted, report.test_rows_deleted, report.snapshots_deleted) == (2, 2, 2)
    assert report.errors == ()
    assert get_repository().get_run(ids["old"]) is None
    assert get_repository().get_run(ids["keep"]) is not None
    assert not get_test_result_store().has_rows(ids["older"])
    assert get_test_result_store().has_rows(ids["keep"])
    assert not (tmp_path / "static" / "history" / ids["old"]).exists()
    assert (tmp_path / "static" / "history" / ids["keep"]).is_dir()


def test_prune_with_nothing_expired_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "history")
    monkeypatch.setattr(retention, "datetime", _FrozenDatetime)
    fresh = _record_run(cycle="unit", days_ago=1)

    report = retention.prune_history(HistoryRetention(days=30, runs_per_cycle=10))
    assert report == retention.PruneReport()
    assert get_repository().get_run(fresh) is not None


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]  # noqa: ANN001
        return NOW if tz is None else NOW.astimezone(tz)


# --- CLI ------------------------------------------------------------------------


def _config(tmp_path: Path, history: str) -> Path:
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(
        "version: 1\n"
        "cycles:\n  unit:\n    stages:\n      - name: unit\n        equipment: pytest\n"
        f"{history}",
        encoding="utf-8",
    )
    return cfg


def test_cli_history_prunes_by_the_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "history")
    monkeypatch.setattr(retention, "datetime", _FrozenDatetime)
    old = _record_run(cycle="unit", days_ago=100)
    new = _record_run(cycle="unit", days_ago=1)
    cfg = _config(tmp_path, "history:\n  retention:\n    days: 30\n")

    dry = CliRunner().invoke(app, ["clean", "--history", "--dry-run", "-c", str(cfg)])
    assert dry.exit_code == 0, dry.output
    assert "1 run(s) would be deleted" in dry.output and old in dry.output
    assert get_repository().get_run(old) is not None

    result = CliRunner().invoke(app, ["clean", "--history", "--yes", "-c", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "Deleted 1 run(s)" in result.output
    assert get_repository().get_run(old) is None
    assert get_repository().get_run(new) is not None


def test_cli_history_flags_override_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "history")
    runs = [_record_run(cycle="unit", days_ago=i) for i in range(4)]  # newest first
    cfg = _config(tmp_path, "")

    result = CliRunner().invoke(
        app, ["clean", "--history", "-y", "--keep-runs", "2", "-c", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert "Deleted 2 run(s)" in result.output
    assert get_repository().get_run(runs[0]) is not None
    assert get_repository().get_run(runs[3]) is None


def test_cli_history_refuses_without_limits_or_yes(tmp_path: Path) -> None:
    cfg = _config(tmp_path, "")
    no_limits = CliRunner().invoke(app, ["clean", "--history", "-y", "-c", str(cfg)])
    assert no_limits.exit_code == 2
    assert "history.retention" in no_limits.output

    no_yes = CliRunner().invoke(app, ["clean", "--history", "--keep-days", "7", "-c", str(cfg)])
    assert no_yes.exit_code == 2
    assert "--yes" in no_yes.output


def test_cli_history_leaves_artifacts_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "history")
    cfg = _config(tmp_path, "history:\n  retention:\n    days: 30\n")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["clean", "--history", "-y", "-c", str(cfg)])
    assert result.exit_code == 0, result.output
    assert artifacts.is_dir()


# --- chosen runs -------------------------------------------------------------------


def test_delete_runs_removes_only_the_chosen_finished_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "history")
    gone, kept = _record_run(cycle="unit", days_ago=1), _record_run(cycle="unit", days_ago=2)
    running = get_repository().create_run(status=RunStatus.RUNNING, metadata={"plan": "unit"})
    (tmp_path / "history" / gone).mkdir(parents=True)

    dry = retention.delete_runs([gone, gone, str(running.id), "nope"], dry_run=True)
    assert dry.run_ids == (gone,)
    assert get_repository().get_run(gone) is not None

    report = retention.delete_runs([gone, str(running.id), "nope"])
    assert (report.runs_deleted, report.test_rows_deleted, report.snapshots_deleted) == (1, 1, 1)
    assert report.skipped_running == (str(running.id),)
    assert report.not_found == ("nope",)
    assert get_repository().get_run(gone) is None
    assert get_repository().get_run(kept) is not None
    assert get_repository().get_run(str(running.id)) is not None


def test_cli_history_run_deletes_just_those_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", tmp_path / "history")
    gone, kept = _record_run(cycle="unit", days_ago=1), _record_run(cycle="unit", days_ago=2)

    no_yes = CliRunner().invoke(app, ["clean", "--history", "--run", gone])
    assert no_yes.exit_code == 2 and "--yes" in no_yes.output

    result = CliRunner().invoke(app, ["clean", "--history", "--run", gone, "-y"])
    assert result.exit_code == 0, result.output
    assert "Deleted 1 run(s)" in result.output
    assert get_repository().get_run(gone) is None
    assert get_repository().get_run(kept) is not None

    missing = CliRunner().invoke(app, ["clean", "--history", "--run", gone, "-y"])
    assert missing.exit_code == 2 and "No run" in missing.output

    without_history = CliRunner().invoke(app, ["clean", "--run", kept, "-y"])
    assert without_history.exit_code == 2 and "--history" in without_history.output
