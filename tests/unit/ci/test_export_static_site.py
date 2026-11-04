"""Behaviour of the static-site export that the demo story depends on."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "export_static_site.py"


def _load_exporter_module():
    spec = importlib.util.spec_from_file_location("export_static_site", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Resp:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    """Records the calls ``generate_ai_summaries`` makes against the API."""

    def __init__(self, summaries: dict[str, dict[str, Any]]) -> None:
        self.summaries = summaries
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def put(self, path: str, json: dict[str, Any]) -> _Resp:
        self.calls.append(("PUT", path, json))
        return _Resp(200, {})

    def post(self, path: str, json: dict[str, Any]) -> _Resp:
        self.calls.append(("POST", path, json))
        run_id = path.split("/")[4]
        if run_id not in self.summaries:
            raise ConnectionResetError("provider hung up")
        return _Resp(200, self.summaries[run_id])


def _exporter(module, client: _FakeClient):
    exporter = object.__new__(module.Exporter)
    exporter.client = client
    exporter.skipped = []
    exporter.ai_summaries = 0
    return exporter


def test_only_cycles_with_an_exported_run_are_published() -> None:
    module = _load_exporter_module()
    listing = {
        "config_path": "qasight.yaml",
        "items": [{"name": "sample-pytests"}, {"name": "self-test"}, {"name": "fake-api"}],
    }
    runs = [{"run_id": "a", "cycle": "fake-api"}, {"run_id": "b", "cycle": "self-test"}]

    filtered = module._ran_cycles_only(listing, runs)

    assert [c["name"] for c in filtered["items"]] == ["self-test", "fake-api"]
    assert filtered["config_path"] == "qasight.yaml"


def test_ai_summaries_are_skipped_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    module = _load_exporter_module()
    client = _FakeClient({})
    exporter = _exporter(module, client)

    exporter.generate_ai_summaries(["run-1"])

    assert client.calls == []
    assert exporter.ai_summaries == 0


def test_ai_summaries_go_through_the_api_with_the_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("TESTO_DEMO_AI_MODEL", raising=False)
    module = _load_exporter_module()
    client = _FakeClient(
        {
            "ok": {"status": "available", "summary_text": "Route /broken returns 500."},
            "bad": {"status": "no_summary_generated", "error_code": "provider_timeout"},
        }
    )
    exporter = _exporter(module, client)

    exporter.generate_ai_summaries(["ok", "bad"])

    method, path, config = client.calls[0]
    assert (method, path) == ("PUT", "/api/v1/ai/config")
    assert config["enabled"] is True
    assert config["provider"] == "anthropic"
    assert config["api_key_source"] == "env"
    assert config["model"] == module.DEFAULT_DEMO_AI_MODEL
    # The key stays in the environment; it is never sent through the config.
    assert "test-key" not in str(config)
    assert [c[1] for c in client.calls[1:]] == [
        "/api/v1/runs/ok/ai-summary:generate",
        "/api/v1/runs/bad/ai-summary:generate",
    ]
    # Reuse a summary stored by an earlier pipeline instead of paying for it again.
    assert all(c[2] == {"force_refresh": False} for c in client.calls[1:])
    assert exporter.ai_summaries == 1
    assert exporter.skipped == ["AI summary for bad (provider_timeout)"]


def test_a_failing_summary_does_not_stop_the_export(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    module = _load_exporter_module()
    client = _FakeClient({"ok": {"status": "available"}})
    exporter = _exporter(module, client)

    exporter.generate_ai_summaries(["boom", "ok"])

    assert exporter.ai_summaries == 1
    assert exporter.skipped == ["AI summary for boom (ConnectionResetError)"]


def test_run_cases_are_read_once_per_run_and_the_cache_ends_with_the_export() -> None:
    """The per-test pages reread every run's cases per test; the export reads each run once."""
    module = _load_exporter_module()
    from qasight.services import flaky_service

    original = flaky_service._cases_for_run
    loads: list[str] = []

    class _Run:
        run_id = "r1"

    def fake_load(run: Any) -> dict[str, dict[str, Any]]:
        loads.append(run.run_id)
        return {"t": {"status": "passed"}}

    flaky_service._cases_for_run = fake_load
    try:
        with module._cache_run_cases():
            first = flaky_service._cases_for_run(_Run())
            assert flaky_service._cases_for_run(_Run()) is first
        assert loads == ["r1"]
        assert flaky_service._cases_for_run is fake_load
    finally:
        flaky_service._cases_for_run = original
