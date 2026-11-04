"""Report-DB archive hand-off tests for ``qasight run`` (QA Strategies CLI/EC rows).

``try_persist_cycle_report`` is stubbed at its source module —
``CycleRunService`` (``qasight.services.cycle_run``) imports it lazily
when archiving — so these tests assert the CLI contract
(when it is called, with what) without touching a real database.

Archive *failures* are currently best-effort: the service swallows exceptions
and returns ``None``, so they do not bump the process exit code.  The
QA Strategies rows EC-03c–e / EC-06 describing archive-failure exit codes are
documented as gaps.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import qasight.services.report_archive as report_archive
from qasight.cli.app import app
from tests.fixtures.engine import (
    stage_spec,
    use_echo_adapter,
    write_minimal_config,
    write_multi_stage_config,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


class _ArchiveSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.called = threading.Event()

    def __call__(
        self, *, artifacts_root: Path, plan_name: str, exit_code_override: int | None = None
    ) -> None:
        self.calls.append(
            {
                "artifacts_root": Path(artifacts_root),
                "plan_name": plan_name,
                "exit_code_override": exit_code_override,
                "thread": threading.current_thread().name,
            }
        )
        self.called.set()
        return None


@pytest.fixture()
def archive_spy(monkeypatch: pytest.MonkeyPatch) -> _ArchiveSpy:
    spy = _ArchiveSpy()
    monkeypatch.setattr(report_archive, "try_persist_cycle_report", spy)
    return spy


def test_default_run_archives_synchronously_with_plan_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, archive_spy: _ArchiveSpy
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = cli_runner.invoke(app, ["run", "--cycle", "smoke", "--config", str(cfg)])

    assert result.exit_code == 0
    assert len(archive_spy.calls) == 1
    call = archive_spy.calls[0]
    assert call["plan_name"] == "smoke"
    assert call["exit_code_override"] == 0
    assert call["artifacts_root"] == (tmp_path / "artifacts").resolve()
    assert call["thread"] == "MainThread"  # sync by default


def test_failing_run_archives_with_override_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, archive_spy: _ArchiveSpy
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_multi_stage_config(
        tmp_path, stages=[stage_spec("fails", args=["--exit-code", "1"])]
    )

    result = cli_runner.invoke(app, ["run", "--cycle", "multi", "--config", str(cfg)])

    assert result.exit_code == 1
    assert archive_spy.calls[0]["exit_code_override"] == 1


def test_no_report_db_skips_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, archive_spy: _ArchiveSpy
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = cli_runner.invoke(
        app, ["run", "--cycle", "smoke", "--config", str(cfg), "--no-report-db"]
    )

    assert result.exit_code == 0
    assert archive_spy.calls == []


def test_no_persist_also_skips_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, archive_spy: _ArchiveSpy
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = cli_runner.invoke(
        app, ["run", "--cycle", "smoke", "--config", str(cfg), "--no-persist"]
    )

    assert result.exit_code == 0
    assert archive_spy.calls == []


def test_async_report_db_archives_on_background_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner, archive_spy: _ArchiveSpy
) -> None:
    use_echo_adapter(monkeypatch)
    cfg = write_minimal_config(tmp_path)

    result = cli_runner.invoke(
        app, ["run", "--cycle", "smoke", "--config", str(cfg), "--async-report-db"]
    )

    assert result.exit_code == 0
    assert archive_spy.called.wait(timeout=5.0), "async archive thread never ran"
    assert archive_spy.calls[0]["thread"] == "testo-report-archive"
    assert archive_spy.calls[0]["exit_code_override"] == 0


def test_archive_failure_does_not_change_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_runner: CliRunner
) -> None:
    # Locks current best-effort semantics (QA Strategies EC-03c–e/EC-06 gap):
    # a broken archive layer must not fail an otherwise green run.
    use_echo_adapter(monkeypatch)
    monkeypatch.setattr(
        report_archive,
        "try_persist_cycle_report",
        lambda **_: None,  # service returns None when archiving failed
    )
    cfg = write_minimal_config(tmp_path)

    result = cli_runner.invoke(app, ["run", "--cycle", "smoke", "--config", str(cfg)])

    assert result.exit_code == 0
