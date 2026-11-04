"""Update notice (qasight/services/update_check.py), doctor's row and GET /api/v1/version."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.services import update_check as uc

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in (
        uc.OFF_ENV,
        "CI",
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "JENKINS_URL",
        "CIRCLECI",
        "BUILDKITE",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_versions_compare_as_numbers() -> None:
    assert uc.is_newer("1.10.0", "1.9.2")
    assert not uc.is_newer("1.5.0", "1.5.0")
    assert uc.is_newer("1.6.0", "1.6.0rc1")  # the final release of a pre-release
    assert not uc.is_newer("1.5.9", "1.6.0rc1")
    assert uc.is_newer("1.6.1", "1.6.0.dev3")
    assert not uc.is_newer("2.0.0b1", "1.5.0")  # pre-releases are never announced


def test_off_in_ci_and_with_the_switch() -> None:
    assert uc.enabled({})
    assert not uc.enabled({"CI": "true"})
    assert not uc.enabled({"GITHUB_ACTIONS": "true"})
    assert not uc.enabled({uc.OFF_ENV: "1"})


def test_pypi_is_asked_once_a_day_and_failures_are_cached(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float] = []

    def fake_fetch(timeout: float) -> str:
        calls.append(timeout)
        return "9.0.0"

    monkeypatch.setattr(uc, "_fetch", fake_fetch)
    assert uc.latest_version(now=1000.0) == "9.0.0"
    assert uc.latest_version(now=1000.0 + 3600) == "9.0.0"
    assert len(calls) == 1
    assert json.loads(uc.cache_path().read_text())["latest"] == "9.0.0"

    def offline(timeout: float) -> str:
        raise OSError("no network")

    monkeypatch.setattr(uc, "_fetch", offline)
    assert uc.latest_version(now=1000.0 + uc.CACHE_SECONDS + 1) is None
    monkeypatch.setattr(uc, "_fetch", fake_fetch)
    # The failure is cached too, so an offline machine doesn't wait on every call.
    assert uc.latest_version(now=1000.0 + uc.CACHE_SECONDS + 2) is None


def test_notice_names_the_command(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uc, "_fetch", lambda timeout: "99.0.0")
    info = uc.check()
    assert info.available
    assert (
        info.notice() == f"qasight 99.0.0 is out (you have {info.current}): pip install -U qasight"
    )


def test_doctor_shows_the_newer_version(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from qasight.cli.app import app

    monkeypatch.setattr(uc, "_fetch", lambda timeout: "99.0.0")
    (home / "qasight.yaml").write_text("cycles: {}\n")
    monkeypatch.chdir(home)

    result = CliRunner().invoke(app, ["doctor"], env={"COLUMNS": "200"})

    assert "99.0.0 is out" in result.output


def test_version_route(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from qasight_api.main import create_app

    monkeypatch.setattr(uc, "_fetch", lambda timeout: "99.0.0")
    body = TestClient(create_app()).get("/api/v1/version").json()

    assert body["latest"] == "99.0.0" and body["update_available"] is True
    assert body["version"] == uc.current_version()
