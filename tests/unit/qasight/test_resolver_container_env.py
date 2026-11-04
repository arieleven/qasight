"""``${env:...}`` can't copy the server's secrets into a container stage."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.resolver import interpolation_env, resolve_stages_for_plan
from qasight.config.schema import Plan, Stage

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _plan() -> Plan:
    stage = Stage(
        name="s",
        framework="pytest",
        target_repo=Path("."),
        args=("${env:TESTO_API_TOKEN:-none}", "${env:CI_FLAG}"),
        extra_env=(("LEAK", "${env:DATABASE_URL:-none}|${env:OPENAI_API_KEY:-none}"),),
    )
    return Plan(name="p", description=None, stages=(stage,))


@pytest.fixture(autouse=True)
def _server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_API_TOKEN", "admin-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw@db/testo")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server")
    monkeypatch.setenv("CI_FLAG", "yes")


def test_container_stages_cannot_read_server_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTO_RUNNER", "container")
    (stage,) = resolve_stages_for_plan(_plan())
    assert stage.args == ("none", "yes")
    assert dict(stage.extra_env) == {"LEAK": "none|none"}
    assert "TESTO_API_TOKEN" not in interpolation_env()


def test_host_stages_keep_reading_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESTO_RUNNER", raising=False)
    (stage,) = resolve_stages_for_plan(_plan())
    assert stage.args == ("admin-token", "yes")
