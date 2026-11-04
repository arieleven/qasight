"""Contract for ``/api/v1/resources``: the leases every laptop and CI agent shares on a team server."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from qasight.repository.db_config import reset_engine_cache
from qasight.resources.client import RemoteLeases, acquire, default_holder
from qasight.resources.pools import parse_pool
from qasight.resources.store import LeaseError
from qasight_api.main import create_app

_TOKEN = "s3cret-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_SPEC = {"items": ["vm-1", {"id": "vm-2", "labels": ["win"], "data": {"address": "10.0.0.2"}}]}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setenv("TESTO_API_TOKEN", _TOKEN)
    for name in ("TESTO_AUTH", "TESTO_PROTECT_READS", "TESTO_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    reset_engine_cache()
    yield TestClient(create_app())
    reset_engine_cache()


def _take(client: TestClient, **extra: object) -> dict:
    body = {
        "pool": "vms",
        "spec": _SPEC,
        "holder": {"user": "dana", "host": "laptop-7", "worker": "gw0"},
    }
    resp = client.post("/api/v1/resources/leases", json={**body, **extra}, headers=_AUTH)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_take_heartbeat_release(client: TestClient) -> None:
    # Writes need the token, like starting a run.
    assert (
        client.post("/api/v1/resources/leases", json={"pool": "vms", "spec": _SPEC}).status_code
        == 401
    )

    taken = _take(client, labels=["win"])
    lease = taken["lease"]
    assert (lease["item"], lease["labels"], lease["data"]) == (
        "vm-2",
        ["win"],
        {"address": "10.0.0.2"},
    )
    assert lease["holder"]["user"] == "dana"
    assert len(lease["lease_id"]) == 32

    listed = client.get("/api/v1/resources").json()["pools"]
    assert [(p["name"], p["free"], p["waiting"]) for p in listed] == [("vms", 1, 0)]
    held = next(i for i in listed[0]["items"] if i["lease"])
    # Anyone who can read sees who holds a key, never the lease id that controls it.
    assert held["lease"]["holder"]["host"] == "laptop-7"
    assert "lease_id" not in held["lease"]

    path = f"/api/v1/resources/leases/{lease['lease_id']}"
    assert client.post(f"{path}/heartbeat", headers=_AUTH).status_code == 200
    assert client.delete(path, headers=_AUTH).status_code == 204
    assert client.delete(path, headers=_AUTH).status_code == 404
    assert client.post(f"{path}/heartbeat", headers=_AUTH).status_code == 404


def test_busy_and_no_items_answers(client: TestClient) -> None:
    _take(client)
    _take(client)
    busy = _take(client, waiter_id="w1")
    assert (busy["lease"], busy["reason"], busy["free"]) == (None, "busy", 0)
    assert (
        client.get("/api/v1/resources", params={"pool": "vms"}).json()["pools"][0]["waiting"] == 1
    )
    assert client.delete("/api/v1/resources/waiters/w1", headers=_AUTH).status_code == 204
    assert _take(client, labels=["gpu"])["reason"] == "no_items"
    bad = client.post(
        "/api/v1/resources/leases", json={"pool": "vms", "spec": {"items": []}}, headers=_AUTH
    )
    assert bad.status_code == 422


def test_disable_enable_and_take_back(client: TestClient) -> None:
    lease = _take(client, labels=["win"])["lease"]
    item = {"pool": "vms", "item": "vm-2"}
    assert (
        client.post("/api/v1/resources/items/release", json=item, headers=_AUTH).status_code == 204
    )
    assert (
        client.post(
            f"/api/v1/resources/leases/{lease['lease_id']}/heartbeat", headers=_AUTH
        ).status_code
        == 404
    )
    assert (
        client.post("/api/v1/resources/items/release", json=item, headers=_AUTH).status_code == 404
    )

    body = {**item, "reason": "snapshot broken"}
    assert (
        client.post("/api/v1/resources/items/disable", json=body, headers=_AUTH).status_code == 204
    )
    assert _take(client, labels=["win"])["reason"] == "no_items"
    status = next(
        i
        for i in client.get("/api/v1/resources").json()["pools"][0]["items"]
        if i["item"] == "vm-2"
    )
    assert (status["disabled"], status["disabled_reason"]) == (True, "snapshot broken")
    assert (
        client.post("/api/v1/resources/items/enable", json=item, headers=_AUTH).status_code == 204
    )
    assert _take(client, labels=["win"])["lease"]["item"] == "vm-2"
    missing = {"pool": "vms", "item": "nope"}
    assert (
        client.post("/api/v1/resources/items/disable", json=missing, headers=_AUTH).status_code
        == 404
    )


def test_taking_a_key_back_needs_an_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    from qasight_api.security import required_role

    def role(path: str) -> str:
        return required_role(
            Request({"type": "http", "method": "POST", "path": path, "headers": []})
        )

    assert role("/api/v1/resources/items/release") == "admin"
    assert role("/api/v1/resources/leases") == "runner"
    assert role("/api/v1/resources/items/disable") == "runner"


def test_the_cli_client_speaks_this_api(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def through_app(method: str, url: str, **kwargs: object) -> object:
        kwargs.pop("timeout", None)
        return client.request(method, url.removeprefix("https://testo.example"), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(requests, "request", through_app)
    remote = RemoteLeases("https://testo.example", _TOKEN)
    pool = parse_pool("vms", _SPEC)
    with acquire(
        remote, pool, labels=["win"], holder=default_holder(run_id="r1"), wait_s=0
    ) as held:
        assert (held.id, held.env()["TESTO_RESOURCE_ADDRESS"]) == ("vm-2", "10.0.0.2")
        assert remote.available(pool) == 1
        [status] = remote.status()
        assert next(i for i in status.items if i.lease).lease.holder.run_id == "r1"  # type: ignore[union-attr]
        assert remote.heartbeat(held.lease.lease_id) is not None
    assert remote.available(pool) == 2
    assert remote.set_disabled("vms", "vm-1", disabled=True, reason="x") is True
    assert remote.force_release("vms", "vm-1") is False

    with pytest.raises(LeaseError, match="answered 401"):
        RemoteLeases("https://testo.example", "wrong").take(pool, holder=default_holder())


def test_a_clean_release_reaches_the_next_taker(client: TestClient) -> None:
    first = _take(client, labels=["win"])["lease"]
    assert first["clean"] is False
    path = f"/api/v1/resources/leases/{first['lease_id']}"
    assert client.delete(f"{path}?clean=true", headers=_AUTH).status_code == 204
    win = next(
        i
        for i in client.get("/api/v1/resources").json()["pools"][0]["items"]
        if i["item"] == "vm-2"
    )
    assert win["clean"] is True
    second = _take(client, labels=["win"])["lease"]
    assert second["clean"] is True
    # Taken again, so no longer known to be clean.
    win = next(
        i
        for i in client.get("/api/v1/resources").json()["pools"][0]["items"]
        if i["item"] == "vm-2"
    )
    assert win["clean"] is False and win["lease"]["reuse_delay_s"] == pytest.approx(40.0)


def test_the_server_keeps_the_provider_log_and_never_runs_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.resources.store import ProviderAction

    action = {"pool": "vms", "item": "testo-vm-1", "verb": "create", "ok": True}
    assert client.post("/api/v1/resources/actions", json=action).status_code == 401
    assert (
        client.post(
            "/api/v1/resources/actions", json={**action, "verb": "reboot"}, headers=_AUTH
        ).status_code
        == 422
    )

    def through_app(method: str, url: str, **kwargs: object) -> object:
        kwargs.pop("timeout", None)
        return client.request(method, url.removeprefix("https://testo.example"), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(requests, "request", through_app)
    remote = RemoteLeases("https://testo.example", _TOKEN)
    remote.record_action(
        ProviderAction(
            pool="vms",
            item="testo-vm-1",
            verb="destroy",
            user="dana",
            host="laptop-7",
            command="./vms.sh delete",
            ok=False,
            detail="exit 1: not found",
        )
    )
    [logged] = remote.actions("vms")
    assert (logged.verb, logged.user, logged.ok, logged.detail) == (
        "destroy",
        "dana",
        False,
        "exit 1: not found",
    )
    assert logged.at > 0  # the server's clock
    assert client.get("/api/v1/resources/actions?pool=other").json() == {"actions": []}
