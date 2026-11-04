"""Tests for ``qasight watch``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.cli.commands import watch as watch_mod
from qasight.engine.exit_codes import EngineExitCode


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_watch_requires_cycle_option(runner: CliRunner) -> None:
    result = runner.invoke(app, ["watch"])
    assert result.exit_code != 0
    assert "cycle" in result.stdout.lower() or "cycle" in (result.stderr or "").lower()


def test_watch_invalid_path_exits_invalid_input(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["watch", "--cycle", "c", "--path", "does-not-exist"])
    assert result.exit_code == int(EngineExitCode.INVALID_INPUT)
    assert "not a directory" in result.stdout


@pytest.mark.parametrize(
    "path_parts,expected",
    [
        (("repo", "src", "main.py"), False),
        (("repo", ".git", "HEAD"), True),
        (("repo", "artifacts", "run.json"), True),
        (("repo", "sub", "__pycache__", "x.pyc"), True),
        (("repo", "node_modules", "x.js"), True),
    ],
)
def test_should_ignore(path_parts: tuple[str, ...], expected: bool) -> None:
    assert watch_mod._should_ignore(Path(*path_parts)) is expected


def test_watch_runs_observer_lifecycle_and_exits_zero(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise ``watch()`` end-to-end with a fake, never-alive Observer so the loop exits immediately."""
    monkeypatch.chdir(tmp_path)

    calls: dict[str, int] = {"schedule": 0, "start": 0, "stop": 0, "join": 0}

    class _FakeObserver:
        def schedule(self, *_a: object, **_k: object) -> None:
            calls["schedule"] += 1

        def start(self) -> None:
            calls["start"] += 1

        def is_alive(self) -> bool:
            return False

        def stop(self) -> None:
            calls["stop"] += 1

        def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
            calls["join"] += 1

    import watchdog.observers

    monkeypatch.setattr(watchdog.observers, "Observer", _FakeObserver)

    result = runner.invoke(app, ["watch", "--cycle", "c", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert calls == {"schedule": 1, "start": 1, "stop": 1, "join": 1}


class _Event:
    def __init__(self, src_path: str, *, is_directory: bool = False) -> None:
        self.src_path = src_path
        self.is_directory = is_directory


def _watch_with_events(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[_Event],
    exit_code: int = 0,
) -> tuple[list[dict[str, object]], str]:
    """Run ``qasight watch`` with a fake Observer that replays ``events`` once.

    The handler's debounce timers fire on their own threads, so the fake stays
    alive until a run happened (or a short deadline passes), and ``join`` waits
    for the timers so their console output is captured before the CLI exits.
    """
    import threading
    import time

    import watchdog.observers

    runs: list[dict[str, object]] = []
    ran = threading.Event()

    def fake_execute(**kwargs: object) -> int:
        runs.append(kwargs)
        ran.set()
        return exit_code

    import qasight.cli.runner as runner_mod

    monkeypatch.setattr(runner_mod, "execute_plan_command", fake_execute)

    class _ReplayObserver:
        def __init__(self) -> None:
            self._handler = None
            self._deadline = 0.0

        def schedule(self, handler, *_a: object, **_k: object) -> None:  # noqa: ANN001
            self._handler = handler

        def start(self) -> None:
            for event in events:
                self._handler.on_any_event(event)  # type: ignore[union-attr]
            self._deadline = time.monotonic() + 0.5

        def is_alive(self) -> bool:
            ran.wait(timeout=0.05)
            return not ran.is_set() and time.monotonic() < self._deadline

        def stop(self) -> None:
            pass

        def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
            for thread in threading.enumerate():
                if isinstance(thread, threading.Timer):
                    thread.join(timeout=5)

    monkeypatch.setattr(watchdog.observers, "Observer", _ReplayObserver)
    result = runner.invoke(
        app, ["watch", "--cycle", "smoke", "--path", str(tmp_path), "--debounce-ms", "50"]
    )
    assert result.exit_code == 0, result.stdout
    return runs, result.stdout


def test_a_burst_of_changes_runs_the_cycle_once(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src.py"
    runs, out = _watch_with_events(
        runner, tmp_path, monkeypatch, [_Event(str(src)), _Event(str(src)), _Event(str(src))]
    )

    assert len(runs) == 1
    assert runs[0]["plan_name"] == "smoke"
    assert runs[0]["persist"] is True
    assert "Change detected" in out
    assert "completed" in out


def test_ignored_and_directory_events_do_not_run(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, _ = _watch_with_events(
        runner,
        tmp_path,
        monkeypatch,
        [
            _Event(str(tmp_path / ".git" / "index")),
            _Event(str(tmp_path / "pkg"), is_directory=True),
            _Event(""),
        ],
    )

    assert runs == []


def test_a_failing_run_is_reported(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs, out = _watch_with_events(
        runner, tmp_path, monkeypatch, [_Event(str(tmp_path / "a.py"))], exit_code=1
    )

    assert len(runs) == 1
    assert "exited with code 1" in out
