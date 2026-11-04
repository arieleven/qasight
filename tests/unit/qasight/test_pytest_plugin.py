"""``pytest --qasight`` records the session as a run (qasight/pytest_plugin.py).

Each test runs pytest in a subprocess on a two-test project, as a user would.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS = """
def test_ok():
    assert True


def test_bad():
    assert 1 == 2
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_demo.py").write_text(_TESTS, encoding="utf-8")
    # A rootdir marker, so pytest doesn't walk up into this repo's config.
    (root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return root


def _pytest(root: Path, db: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    environ = {k: v for k, v in os.environ.items() if not k.startswith(("TESTO_", "PYTEST_"))}
    environ.update({"DATABASE_URL": f"sqlite:///{db}", **env})
    for ci_var in ("CI", "GITHUB_ACTIONS", "GITLAB_CI"):
        environ.pop(ci_var, None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=root,
        env=environ,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _runs(db: Path) -> list[tuple[str | None, int, int | None]]:
    code = (
        "from qasight.history.read_model import list_recent_runs\n"
        "print([(r.cycle, r.returncode, r.failed) for r in list_recent_runs(limit=10)])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "DATABASE_URL": f"sqlite:///{db}"},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return eval(out.stdout.strip())  # noqa: S307 — our own repr of a list of tuples


def test_records_the_session_and_keeps_pytests_exit_code(project: Path, tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    result = _pytest(project, db, "--qasight-cycle", "shop-unit")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "qasight: recorded this session as a run of cycle 'shop-unit'" in result.stdout
    assert _runs(db) == [("shop-unit", 1, 1)]
    # The temporary report folder is gone.
    assert not list(project.glob(".testo-*"))


def test_inert_without_the_flag(project: Path, tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    result = _pytest(project, db)
    assert result.returncode == 1
    assert "qasight:" not in result.stdout
    assert not db.exists() or _runs(db) == []


def test_a_recording_problem_never_changes_the_outcome(project: Path, tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    # Nothing listens on port 9: the upload fails, the tests' result stands.
    result = _pytest(project, db, "--qasight", "--qasight-server", "http://127.0.0.1:9", "-k", "ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "qasight: could not record this session" in result.stdout


# -- quarantine -------------------------------------------------------------------


def _quarantine(db: Path, cycle: str, test: str) -> None:
    from sqlmodel import create_engine

    from qasight.services.quarantine_store import QuarantineStore

    engine = create_engine(f"sqlite:///{db}")
    try:
        QuarantineStore(engine).add(cycle=cycle, test=test)
    finally:
        engine.dispose()


def test_a_session_whose_failures_are_all_quarantined_exits_0(
    project: Path, tmp_path: Path
) -> None:
    db = tmp_path / "history.db"
    _quarantine(db, "shop-unit", "test_bad")
    result = _pytest(project, db, "--qasight-cycle", "shop-unit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "qasight: exit 0 because every failed test is quarantined in cycle 'shop-unit': test_bad"
        in result.stdout
    )
    # Mute, not skip: the failure is still recorded, and the run passed the gate too.
    assert _runs(db) == [("shop-unit", 0, 1)]


def test_config_rules_count_and_another_failure_still_fails(project: Path, tmp_path: Path) -> None:
    (project / "qasight.yaml").write_text(
        "version: 1\ncycles:\n  shop-unit:\n    quarantine: [test_bad]\n"
        "    stages:\n      - {name: unit, framework: pytest}\n",
        encoding="utf-8",
    )
    db = tmp_path / "history.db"
    result = _pytest(project, db, "--qasight-cycle", "shop-unit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "quarantined in cycle 'shop-unit': test_bad" in result.stdout

    (project / "tests" / "test_more.py").write_text(
        "def test_other():\n    assert False\n", encoding="utf-8"
    )
    result = _pytest(project, db, "--qasight-cycle", "shop-unit")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "quarantined in cycle" not in result.stdout


def test_quarantine_is_not_applied_without_the_flag(project: Path, tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    _quarantine(db, "shop", "test_bad")
    assert _pytest(project, db).returncode == 1


def _verbose_lines(stdout: str) -> list[str]:
    """``tests/x.py::test_y PASSED`` lines, in the order the tests ran."""
    return [line for line in stdout.splitlines() if re.search(r"::test_\w+ (PASSED|FAILED)", line)]


def test_failed_first_runs_last_runs_failures_first(project: Path, tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    recorded = _pytest(project, db, "--qasight-cycle", "shop")
    assert recorded.returncode == 1, recorded.stdout + recorded.stderr

    result = _pytest(project, db, "-vv", "--qasight-failed-first", "--qasight-cycle", "shop")
    assert result.returncode == 1, result.stdout + result.stderr
    lines = _verbose_lines(result.stdout)
    assert "test_bad" in lines[0] and "test_ok" in lines[1], result.stdout
    assert "qasight: failed first: 1 failed last time" in result.stdout


def test_failed_first_without_history_keeps_pytests_order(project: Path, tmp_path: Path) -> None:
    result = _pytest(project, tmp_path / "empty.db", "-vv", "--qasight-failed-first")
    assert result.returncode == 1, result.stdout + result.stderr
    lines = _verbose_lines(result.stdout)
    assert "test_ok" in lines[0], result.stdout
    assert "has no recorded runs yet; kept pytest's order" in result.stdout
