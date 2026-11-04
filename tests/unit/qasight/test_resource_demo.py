"""Made-up pools for trying the Resources page: ``qasight lease demo`` and the demo site."""

from __future__ import annotations

import importlib.util
import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.resources.demo import DEMO_POOLS, clear_demo, seed_demo
from qasight.resources.store import Holder, LeaseStore

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def test_seed_fills_the_pools_with_held_free_disabled_and_waiting_keys() -> None:
    store = LeaseStore(create_engine("sqlite://"))
    now = time.time()
    result = seed_demo(store, now=now)
    assert (result.held, result.disabled, result.waiting) == (7, 1, 2)

    pools = {pool.name: pool for pool in store.status(now=now)}
    assert set(pools) == {p.name for p in DEMO_POOLS}
    vms = {item.item: item for item in pools["demo-flow-vms"].items}
    assert vms["demo-vm-01"].lease is not None and vms["demo-vm-01"].lease.holder.user == "alice"
    assert vms["demo-vm-05"].disabled and "snapshot" in vms["demo-vm-05"].disabled_reason
    assert pools["demo-flow-vms"].free == 2
    assert (pools["demo-license-seats"].free, pools["demo-license-seats"].waiting) == (0, 2)
    assert pools["demo-phones"].free == 1
    # Holders look alive: recent heartbeats, taken at different times.
    leases = [i.lease for p in pools.values() for i in p.items if i.lease]
    assert all(now - lease.heartbeat_at < 60 for lease in leases)
    assert len({lease.taken_at for lease in leases}) == len(leases)


def test_seed_twice_gives_the_same_picture_and_clear_gives_the_keys_back() -> None:
    store = LeaseStore(create_engine("sqlite://"))
    seed_demo(store)
    second = seed_demo(store)
    assert (second.held, second.waiting) == (7, 2)

    assert clear_demo(store) == 7
    pools = store.status()
    assert all(item.lease is None and not item.disabled for p in pools for item in p.items)
    assert all(p.waiting == 0 for p in pools)
    # A cleared pool works like any other.
    vms = next(p for p in DEMO_POOLS if p.name == "demo-flow-vms")
    assert store.take(vms, holder=Holder(user="me")).lease is not None


@pytest.fixture
def history_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = f"sqlite:///{tmp_path / 'history.db'}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    for name in ("TESTO_SERVER_URL", "TESTO_LEASE_DB_URL", "TESTO_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    from qasight.repository.db_config import reset_engine_cache

    reset_engine_cache()
    yield url
    reset_engine_cache()


def test_cli_demo_fills_what_the_ui_shows(history_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Even with a team server configured, the demo only touches this machine's database.
    monkeypatch.setenv("TESTO_SERVER_URL", "http://127.0.0.1:9")
    runner = CliRunner()
    result = runner.invoke(app, ["lease", "demo"])
    assert result.exit_code == 0, result.output
    assert "7 keys held, 1 disabled, 2 waiting" in result.output

    from qasight_api.main import create_app

    body = TestClient(create_app()).get("/api/v1/resources").json()
    names = [pool["name"] for pool in body["pools"]]
    assert names == sorted(p.name for p in DEMO_POOLS)
    vms = next(p for p in body["pools"] if p["name"] == "demo-flow-vms")
    assert vms["free"] == 2 and "lease_id" not in json.dumps(body)

    cleared = runner.invoke(app, ["lease", "demo", "--clear"])
    assert cleared.exit_code == 0 and "Gave back 7 demo key(s)" in cleared.output
    body = TestClient(create_app()).get("/api/v1/resources").json()
    assert all(item["lease"] is None for pool in body["pools"] for item in pool["items"])


def test_static_export_seeds_the_demo_pools_only_when_asked(
    history_db: str, tmp_path: Path
) -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "export_static_site.py"
    spec = importlib.util.spec_from_file_location("export_static_site_demo", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    plain = module.Exporter(out_dir=tmp_path / "plain", site_url="", run_limit=1, delta_pairs=0)
    plain.data_dir.mkdir(parents=True)
    plain.export_misc()
    assert json.loads((plain.data_dir / "resources.json").read_text())["pools"] == []

    demo = module.Exporter(
        out_dir=tmp_path / "demo", site_url="", run_limit=1, delta_pairs=0, demo_resources=True
    )
    demo.data_dir.mkdir(parents=True)
    demo.export_misc()
    pools = json.loads((demo.data_dir / "resources.json").read_text())["pools"]
    assert {p["name"] for p in pools} == {p.name for p in DEMO_POOLS}
    assert sum(1 for p in pools for i in p["items"] if i["lease"]) == 7
