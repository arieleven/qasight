"""Per-cycle roles: an account's role on the cycles matching a pattern (``TESTO_AUTH=accounts``)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qasight.accounts import AccountStore
from qasight.project_roles import ProjectRole, ProjectRoleError, ProjectRoleStore, role_on
from qasight.repository.db_config import reset_engine_cache
from qasight_api.login_throttle import login_throttle
from qasight_api.main import create_app
from qasight_api.security import account_store

_TOKEN = "s3cret-token"
_PASSWORD = "correct horse battery"
_MESSAGE = "AssertionError: total 3 != 4"


@pytest.fixture
def accounts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AccountStore]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    monkeypatch.setenv("TESTO_AUTH", "accounts")
    monkeypatch.delenv("TESTO_PROTECT_READS", raising=False)
    monkeypatch.delenv("TESTO_CORS_ORIGINS", raising=False)
    reset_engine_cache()
    account_store.cache_clear()
    login_throttle.reset()
    store = account_store()
    for name, role in (("vera", "viewer"), ("rui", "runner")):
        store.add(name, _PASSWORD, role)
    yield store
    account_store.cache_clear()
    reset_engine_cache()


def _signed_in(username: str) -> TestClient:
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/auth/login",
        data={"username": username, "token": _PASSWORD, "next": "/"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return client


def _mark(client: TestClient, cycle: str) -> int:
    body = {"cycle": cycle, "message": _MESSAGE, "cause": "product_bug"}
    return client.put("/api/v1/triage", json=body).status_code


def test_a_viewer_granted_runner_on_a_pattern_can_write_only_there(accounts: AccountStore) -> None:
    ProjectRoleStore().grant(username="vera", pattern="shop-*", role="runner")
    vera = _signed_in("vera")
    assert _mark(vera, "shop-web") == 200
    assert _mark(vera, "payments") == 403


def test_a_runner_held_to_viewer_on_one_cycle(accounts: AccountStore) -> None:
    ProjectRoleStore().grant(username="rui", pattern="payments", role="viewer")
    rui = _signed_in("rui")
    assert _mark(rui, "shop-web") == 200
    resp = rui.put("/api/v1/quarantine", json={"cycle": "payments", "test": "t"})
    assert resp.status_code == 403 and "payments" in resp.json()["error"]["message"]


def test_the_token_is_an_admin_everywhere(accounts: AccountStore) -> None:
    ProjectRoleStore().grant(username="rui", pattern="*", role="viewer")
    client = TestClient(create_app())
    client.headers["Authorization"] = f"Bearer {_TOKEN}"
    assert _mark(client, "payments") == 200


def test_most_specific_pattern_wins() -> None:
    now = datetime.now(tz=UTC)
    grants = [
        ProjectRole("a", "*", "viewer", None, now),
        ProjectRole("a", "shop-*", "runner", None, now),
        ProjectRole("a", "shop-legacy", "viewer", None, now),
    ]
    assert role_on(grants, "shop-web", "viewer") == "runner"
    assert role_on(grants, "shop-legacy", "admin") == "viewer"
    assert role_on(grants, "other", "admin") == "viewer"
    assert role_on([], "other", "runner") == "runner"


def test_store_validates_and_revokes(accounts: AccountStore) -> None:
    store = ProjectRoleStore()
    with pytest.raises(ProjectRoleError):
        store.grant(username="vera", pattern="x", role="root")
    with pytest.raises(ProjectRoleError):
        store.grant(username="vera", pattern=" ", role="runner")
    store.grant(username="vera", pattern="x", role="runner")
    assert [g.pattern for g in store.list("vera")] == ["x"]
    assert store.revoke(username="vera", pattern="x")
    assert not store.revoke(username="vera", pattern="x")
