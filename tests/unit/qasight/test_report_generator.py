"""Tests for Allure HTML generation helpers (mocked ``subprocess``)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from qasight.reporting import report_generator as rg
from qasight.reporting.report_generator import generate_allure_html, generate_allure_reports


@pytest.fixture
def fake_ok_subprocess() -> MagicMock:
    p = MagicMock()
    p.returncode = 0
    p.stderr = ""
    p.stdout = "ok"
    return MagicMock(return_value=p)


def test_generate_allure_html_success(
    tmp_path: Path, fake_ok_subprocess: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = tmp_path / "allure-results"
    res.mkdir()
    (res / "pytest").mkdir()
    (res / "pytest" / "a-result.json").write_text(
        '{"status":"passed","start":0,"stop":10}',
        encoding="utf-8",
    )
    out = tmp_path / "out"
    monkeypatch.setattr(rg, "publish_allure_index_to_static", lambda **_: out)
    ok, msg, health = generate_allure_html(
        results_dir=res,
        report_dir=out,
        input_dirs=[res / "pytest"],
        subprocess_run=fake_ok_subprocess,
    )
    assert ok is True
    fake_ok_subprocess.assert_called_once()
    call_argv = fake_ok_subprocess.call_args[0][0]
    assert "awesome" in call_argv
    assert "--single-file" in call_argv
    assert str(res / "pytest") in call_argv
    assert isinstance(health, float) and health == 100.0


def test_generate_allure_html_missing_results(tmp_path: Path) -> None:
    ok, msg, health = generate_allure_html(
        results_dir=tmp_path / "nope", report_dir=tmp_path / "out"
    )
    assert ok is False
    assert "does not exist" in msg


def test_invoke_allure_generate_file_not_found(tmp_path: Path) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError()

    res = tmp_path / "allure-results"
    res.mkdir()
    ok, msg, _ = generate_allure_html(
        results_dir=res, report_dir=tmp_path / "rep", subprocess_run=boom
    )
    assert ok is False
    assert "Allure Report 3" in msg or "not found" in msg.lower()


def test_generate_allure_reports_individual_builds_per_framework_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = tmp_path / "allure-results"
    res.mkdir()
    (res / "pytest").mkdir()
    (res / "pytest" / "a-result.json").write_text(
        '{"status":"passed","start":0,"stop":1}', encoding="utf-8"
    )

    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object):
        calls.append(list(argv))
        p = MagicMock()
        p.returncode = 0
        p.stderr = ""
        p.stdout = "ok"
        return p

    monkeypatch.setattr(rg, "publish_allure_index_to_static", lambda **_: None)

    out = generate_allure_reports(
        results_dir=res,
        frameworks=["pytest", "behavex"],
        subprocess_run=fake_run,
    )
    assert "pytest" in out and "behavex" in out
    assert len(calls) == 2
    assert any(str(res / "pytest") in c for c in calls)
    assert any(str(res / "behavex") in c for c in calls)
    assert any("--output" in c for c in calls)


def test_generate_allure_reports_defaults_to_three_allure_frameworks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default framework list covers pytest/behavex/behave_native (Allure JSON only)."""
    res = tmp_path / "allure-results"
    res.mkdir()

    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object):
        calls.append(list(argv))
        p = MagicMock()
        p.returncode = 0
        p.stderr = ""
        p.stdout = "ok"
        return p

    monkeypatch.setattr(rg, "publish_allure_index_to_static", lambda **_: None)
    out = generate_allure_reports(results_dir=res, subprocess_run=fake_run)
    assert set(out.keys()) == {"pytest", "behavex", "behave_native"}
    assert len(calls) == 3


def test_flatten_behavex_nested_results_moves_to_root(tmp_path: Path) -> None:
    behave = tmp_path / "behavex"
    nested = behave / "allure"
    nested.mkdir(parents=True)
    (nested / "x-result.json").write_text(
        '{"status":"passed","start":0,"stop":1}', encoding="utf-8"
    )

    moved = rg._flatten_allure_result_json(root=behave)
    assert moved == 1
    assert (behave / "x-result.json").is_file()
