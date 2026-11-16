"""``qasight import reportportal``: past launches become dated runs, defect types become causes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from qasight.repository.db_config import reset_engine_cache
from qasight.services.rp_import import RpClient, RpImportError, cause_for_issue, import_project

_LAUNCH = {
    "id": 7,
    "uuid": "launch-7",
    "name": "Nightly API",
    "number": 12,
    "startTime": 1_700_000_000_000,
    "endTime": 1_700_000_060_000,
    "status": "FAILED",
    "attributes": [{"key": "branch", "value": "main"}],
}
_ITEMS = [
    {
        "id": 1,
        "name": "orders",
        "type": "SUITE",
        "hasStats": True,
        "startTime": 1_700_000_000_000,
        "status": "FAILED",
    },
    {
        "id": 2,
        "parent": 1,
        "name": "test_create",
        "type": "STEP",
        "hasStats": True,
        "codeRef": "orders.test_create",
        "startTime": 1_700_000_001_000,
        "endTime": 1_700_000_002_000,
        "status": "FAILED",
        "issue": {
            "issueType": "pb001",
            "comment": "checkout total rounding",
            "externalSystemIssues": [
                {"ticketId": "SHOP-12", "url": "https://jira.example.com/browse/SHOP-12"}
            ],
        },
    },
    {
        "id": 3,
        "parent": 1,
        "name": "test_list",
        "type": "STEP",
        "hasStats": True,
        "startTime": 1_700_000_003_000,
        "status": "PASSED",
        "retries": [
            {"id": 30, "name": "test_list", "type": "STEP", "hasStats": True, "status": "FAILED"}
        ],
    },
    {
        "id": 4,
        "parent": 1,
        "name": "test_db",
        "type": "STEP",
        "hasStats": True,
        "startTime": 1_700_000_004_000,
        "status": "FAILED",
        "issue": {"issueType": "ti001"},
    },
]
_ERRORS = {
    "2": "AssertionError: total 10.01 != 10.00\n  at orders",
    "4": "ConnectionError: db at 10.0.0.5 refused",
}


class _Resp:
    def __init__(self, body: Any, status: int = 200) -> None:
        self._body = body
        self.status_code = status
        self.text = str(body)

    def json(self) -> Any:
        return self._body


class _FakeRp:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url: str, params: dict[str, Any], timeout: float) -> _Resp:
        self.calls.append(url.rsplit("/", 1)[-1])
        if self.headers.get("Authorization") != "Bearer key":
            return _Resp({}, 401)
        if url.endswith("/launch"):
            return _Resp({"content": [_LAUNCH], "page": {"totalPages": 1}})
        if url.endswith("/item"):
            return _Resp({"content": _ITEMS, "page": {"totalPages": 1}})
        if url.endswith("/log"):
            message = _ERRORS.get(str(params["filter.eq.item"]))
            return _Resp({"content": [{"message": message}] if message else []})
        return _Resp({}, 404)


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'h.db'}")
    monkeypatch.setenv("TESTO_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    reset_engine_cache()
    yield tmp_path
    reset_engine_cache()


def _client(token: str = "key") -> RpClient:
    return RpClient(url="https://rp.example.com", project="shop", token=token, session=_FakeRp())  # type: ignore[arg-type]


def test_a_launch_becomes_a_dated_run_with_causes(history: Path) -> None:
    from qasight.repository.db import get_repository
    from qasight.services.triage import TriageStore

    report = import_project(_client())
    assert [label for label, _ in report.imported] == ["Nightly API #12"]
    assert report.causes == 1

    record = get_repository().get_run(report.imported[0][1])
    assert record is not None
    meta = record.metadata_
    assert meta["plan"] == "Nightly-API"
    assert meta["reportportal_launch_uuid"] == "launch-7"
    assert int(meta["started_at"]) == 1_700_000_000
    assert meta["ci_ref_name"] == "main"

    causes = TriageStore().list("Nightly-API")
    assert [(c.cause, c.note) for c in causes] == [("product_bug", "checkout total rounding")]
    assert causes[0].example_message == "AssertionError: total 10.01 != 10.00"

    # Running it again skips what is already there.
    again = import_project(_client())
    assert again.imported == [] and again.skipped == ["Nightly API #12"]


def test_bad_key_stops_the_import(history: Path) -> None:
    with pytest.raises(RpImportError, match="401"):
        import_project(_client(token="wrong"))


def test_cause_mapping() -> None:
    assert cause_for_issue("pb001") == "product_bug"
    assert cause_for_issue("ab_custom") == "test_bug"
    assert cause_for_issue("SI001") == "environment"
    assert cause_for_issue("nd001") == "other"
    assert cause_for_issue("ti001") is None
    assert cause_for_issue(None) is None
