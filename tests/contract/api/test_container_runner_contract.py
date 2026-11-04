"""``TESTO_RUNNER=container`` on the API: it won't start half configured."""

from __future__ import annotations

import pytest

from qasight_api.main import run

pytestmark = [pytest.mark.contract, pytest.mark.tier_fast]


def test_container_runner_without_its_settings_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TESTO_RUNNER", "container")
    monkeypatch.delenv("TESTO_RUNNER_URL", raising=False)
    monkeypatch.setenv("TESTO_RUNNER_TOKEN", "t")
    monkeypatch.delenv("TESTO_AUTH", raising=False)
    monkeypatch.setenv("TESTO_API_HOST", "127.0.0.1")
    assert run([]) == 2
    assert "TESTO_RUNNER=container needs TESTO_RUNNER_URL" in capsys.readouterr().err
