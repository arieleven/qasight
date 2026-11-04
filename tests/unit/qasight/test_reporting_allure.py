"""Tests for :mod:`qasight.reporting.allure`, with the Allure CLI faked."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from qasight.reporting import allure


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["allure"], returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def cli_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(allure, "is_allure_available", lambda: True)


def test_generate_without_result_dirs_is_not_ok(tmp_path: Path) -> None:
    result = allure.generate_html(result_dirs=[], out_dir=tmp_path)

    assert result.ok is False
    assert "no allure-results" in result.message


def test_generate_without_the_cli_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(allure, "is_allure_available", lambda: False)

    with pytest.raises(allure.AllureCLINotFoundError, match="--format json/junit"):
        allure.generate_html(result_dirs=[tmp_path], out_dir=tmp_path / "out")


def test_generate_passes_options_and_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_available: None
) -> None:
    seen: dict[str, object] = {}

    def fake_generate(**kwargs: object) -> subprocess.CompletedProcess:
        seen.update(kwargs)
        return _completed(0)

    monkeypatch.setattr(allure, "run_generate", fake_generate)
    monkeypatch.setattr(allure, "report_has_index", lambda _out: True)

    result = allure.generate_html(
        result_dirs=[tmp_path / "r"], out_dir=tmp_path / "out", clean=False, single_file=True
    )

    assert result.ok is True
    assert result.message == "report generated"
    assert result.out_dir == (tmp_path / "out").resolve()
    assert seen == {
        "result_dirs": [tmp_path / "r"],
        "out_dir": (tmp_path / "out").resolve(),
        "clean": False,
        "single_file": True,
    }


@pytest.mark.parametrize(
    ("completed", "has_index", "message"),
    [
        (_completed(1, stderr="boom\n"), False, "boom"),
        (_completed(2), False, "allure exited 2"),
        # Exit 0 without an index.html is still a failed report.
        (_completed(0, stdout="done"), False, "done"),
    ],
)
def test_generate_failure_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli_available: None,
    completed: subprocess.CompletedProcess,
    has_index: bool,
    message: str,
) -> None:
    monkeypatch.setattr(allure, "run_generate", lambda **_k: completed)
    monkeypatch.setattr(allure, "report_has_index", lambda _out: has_index)

    result = allure.generate_html(result_dirs=[tmp_path], out_dir=tmp_path / "out")

    assert result.ok is False
    assert result.message == message


def test_generate_maps_a_vanished_binary_to_cli_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_available: None
) -> None:
    def gone(**_k: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("allure")

    monkeypatch.setattr(allure, "run_generate", gone)

    with pytest.raises(allure.AllureCLINotFoundError):
        allure.generate_html(result_dirs=[tmp_path], out_dir=tmp_path / "out")


def test_serve_without_result_dirs_returns_1() -> None:
    assert allure.serve_results(result_dirs=[]) == 1


def test_serve_without_the_cli_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(allure, "is_allure_available", lambda: False)

    with pytest.raises(allure.AllureCLINotFoundError):
        allure.serve_results(result_dirs=[tmp_path])


def test_serve_delegates_to_allure_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_available: None
) -> None:
    calls: list[tuple[object, int]] = []

    def fake_open(*, paths: object, port: int) -> int:
        calls.append((paths, port))
        return 0

    monkeypatch.setattr(allure, "run_open_blocking", fake_open)

    assert allure.serve_results(result_dirs=[tmp_path], port=9001) == 0
    assert calls == [([tmp_path], 9001)]
