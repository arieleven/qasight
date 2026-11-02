"""``GET /api/v1/storage`` and the disk check in ``/api/v1/health/ready``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from testo_api.main import create_app
from testo_api.routes.storage import forget_cached_storage
from testo_core import paths
from testo_core.history import storage
from testo_core.history.storage import DiskSpace
from testo_core.repository.db import get_repository
from testo_core.repository.models import RunStatus

pytestmark = pytest.mark.tier_fast

GB = 1024**3


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    forget_cached_storage()
    yield
    forget_cached_storage()


def _disk(monkeypatch: pytest.MonkeyPatch, free: int, total: int = 100 * GB) -> None:
    monkeypatch.setattr(storage, "disk_space", lambda path=None: DiskSpace("/data", total, free))


def test_storage_counts_runs_and_sizes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_repository().create_run(status=RunStatus.COMPLETED, metadata={"plan": "web"})
    history = tmp_path / "history"
    (history / "r1").mkdir(parents=True)
    (history / "r1" / "result.json").write_bytes(b"x" * 1000)
    monkeypatch.setattr(paths, "STATIC_HISTORY_ROOT", history)
    _disk(monkeypatch, free=50 * GB)

    body = TestClient(create_app()).get("/api/v1/storage").json()
    assert body["runs"] == 1
    assert body["history_files_bytes"] == 1000
    assert body["report_archives"] == 0
    assert body["disk"]["free_pct"] == 50.0 and body["disk"]["low"] is False


def test_storage_flags_a_low_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    _disk(monkeypatch, free=5 * GB)
    body = TestClient(create_app()).get("/api/v1/storage").json()
    assert body["disk"]["low"] is True


def test_ready_warns_on_low_disk_but_stays_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _disk(monkeypatch, free=5 * GB)
    resp = TestClient(create_app()).get("/api/v1/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"]["disk"]["status"] == "warning"


def test_ready_is_degraded_when_the_disk_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    _disk(monkeypatch, free=10 * 1024 * 1024)
    resp = TestClient(create_app()).get("/api/v1/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"]["disk"]["status"] == "degraded"
