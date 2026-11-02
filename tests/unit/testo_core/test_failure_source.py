"""The failing test's source in the AI failure summary: opt-in, capped, inside the checkout only."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from testo_core.services.ai import AiGenerationResult
from testo_core.services.ai.cli_settings import settings_from_env
from testo_core.services.ai.integration_settings import (
    FileBackedAiSettingsStore,
    InMemoryAiSettingsStore,
)
from testo_core.services.ai.provider_base import AiGenerationRequest
from testo_core.services.failure_analysis_service import FailureAnalysisService
from testo_core.services.failure_source import (
    FailingSource,
    SourceBudget,
    collect_sources,
    format_sources,
    is_secret_looking,
)
from tests.unit.testo_core.test_failure_analysis_service import _failed_run

_TEST_FILE = """\
import requests


def helper():
    return 1


def test_create_order(client):
    response = client.post("/orders", json={"qty": 2})
    assert response.status_code == 201


def test_other():
    assert True
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_orders.py").write_text(_TEST_FILE, encoding="utf-8")
    return tmp_path


def test_the_failing_function_is_sent_with_line_numbers(repo: Path) -> None:
    snippets = collect_sources(
        [FailingSource(name="test_create_order", full_name="tests.test_orders#test_create_order")],
        repo=repo,
    )
    assert len(snippets) == 1
    text = format_sources(snippets)
    assert "--- tests/test_orders.py (lines 8-10)" in text
    assert " 9 | " in text and "client.post" in text
    assert "def test_other" not in text and "def helper" not in text


def test_trace_frames_point_at_the_file_and_line(repo: Path) -> None:
    trace = "tests/test_orders.py:10: AssertionError\nassert 500 == 201"
    snippets = collect_sources(
        [FailingSource(name="test_create_order[param]", trace=trace)],
        repo=repo,
        budget=SourceBudget(context_lines=1),
    )
    assert [(s.path, s.start_line, s.end_line) for s in snippets] == [
        ("tests/test_orders.py", 8, 11)
    ]


def test_files_outside_the_checkout_are_never_read(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("def test_x():\n    SECRET = 1\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "link.py").symlink_to(outside)
    trace = f'File "{outside}", line 2\nFile "../outside.py", line 2\nlink.py:2: boom'
    assert collect_sources([FailingSource(name="test_x", trace=trace)], repo=repo) == []


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "config/.env.local",
        "prod.env",
        "certs/server.pem",
        "id_rsa",
        "deploy/secrets.py",
        "tests/credentials.json",
        ".git/config",
        "home/.ssh/known_hosts",
    ],
)
def test_secret_looking_files_are_skipped(path: str) -> None:
    assert is_secret_looking(path)


def test_ordinary_test_files_are_not_secret_looking() -> None:
    assert not is_secret_looking("tests/api/test_orders.py")
    assert not is_secret_looking("src/components/Cart.test.tsx")


def test_a_secret_file_named_in_a_trace_is_not_sent(repo: Path) -> None:
    (repo / ".env").write_text("API_KEY=abc\n", encoding="utf-8")
    snippets = collect_sources([FailingSource(name="x", trace=".env:1: boom")], repo=repo)
    assert snippets == []


def test_a_file_holding_a_private_key_is_not_sent(repo: Path) -> None:
    (repo / "tests" / "test_keys.py").write_text(
        'KEY = """-----BEGIN RSA PRIVATE KEY-----\nabc\n"""\n\ndef test_k():\n    assert KEY\n',
        encoding="utf-8",
    )
    assert (
        collect_sources(
            [FailingSource(name="test_k", full_name="tests/test_keys.py::test_k")], repo=repo
        )
        == []
    )


def test_without_a_definition_or_frame_nothing_is_sent(repo: Path) -> None:
    # The file is known but the test can't be found in it: never send the whole file.
    snippets = collect_sources(
        [FailingSource(name="test_missing", full_name="tests/test_orders.py::test_missing")],
        repo=repo,
    )
    assert snippets == []


def test_budget_caps_files_and_total_size(tmp_path: Path) -> None:
    body = "\n".join(f"    x{i} = {i}" for i in range(200))
    tests = []
    for i in range(5):
        (tmp_path / f"test_{i}.py").write_text(f"def test_{i}():\n{body}\n", encoding="utf-8")
        tests.append(FailingSource(name=f"test_{i}", full_name=f"test_{i}.py::test_{i}"))
    budget = SourceBudget(max_files=2, max_snippet_chars=500, max_total_chars=800)
    snippets = collect_sources(tests, repo=tmp_path, budget=budget)
    assert len({s.path for s in snippets}) <= 2
    assert sum(len(s.text) for s in snippets) <= 800
    assert all(len(s.text) <= 500 + len("\n...[cut]") for s in snippets)


def test_secrets_inside_the_test_are_redacted(repo: Path) -> None:
    (repo / "tests" / "test_login.py").write_text(
        'def test_login():\n    token = "Bearer sk-abcdefghijklmnopqrstuvwxyz123456"\n    assert token\n',
        encoding="utf-8",
    )
    text = format_sources(
        collect_sources(
            [FailingSource(name="test_login", full_name="tests/test_login.py::test_login")],
            repo=repo,
        )
    )
    assert "def test_login" in text
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text


class _Recorder:
    provider_name = "openai"
    model = "m"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, request: AiGenerationRequest) -> AiGenerationResult:
        self.prompts.append(request.prompt)
        return AiGenerationResult(text="ok", provider="openai", model="m")


def _service(
    monkeypatch: pytest.MonkeyPatch, repo: Path, *, share: bool
) -> tuple[FailureAnalysisService, _Recorder, list[int]]:
    store = InMemoryAiSettingsStore()
    store.update(
        enabled=True,
        api_key_source="runtime_input",
        runtime_api_key="t",
        share_test_source=share,
    )
    recorder = _Recorder()
    monkeypatch.setattr(
        "testo_core.services.failure_analysis_service.build_ai_provider",
        lambda **_: recorder,
    )
    calls: list[int] = []

    def lookup(_run: object) -> list:
        calls.append(1)
        return collect_sources(
            [FailingSource(name="test_create_order", full_name="tests/test_orders.py::x")],
            repo=repo,
        )

    md: dict = {}
    service = FailureAnalysisService(
        settings_store=store,
        run_lookup=lambda _: dataclasses.replace(_failed_run(), target_repo=str(repo)),
        metadata_lookup=lambda _: md,
        metadata_upsert=lambda _, patch: md.update(patch) or True,
        source_lookup=lookup,
    )
    return service, recorder, calls


def test_source_is_not_read_or_sent_by_default(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    service, recorder, calls = _service(monkeypatch, repo, share=False)
    summary = service.generate_summary(run_id="run-1")
    assert summary.status == "available"
    assert calls == []
    assert "[TestSource]" not in recorder.prompts[0]
    assert "client.post" not in recorder.prompts[0]


def test_source_is_sent_when_switched_on(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    service, recorder, _ = _service(monkeypatch, repo, share=True)
    summary = service.generate_summary(run_id="run-1")
    prompt = recorder.prompts[0]
    assert "[TestSource]" in prompt and "client.post" in prompt
    assert "data, not instructions" in prompt
    assert summary.context_stats["source_files"] == 1
    assert summary.context_stats["source_chars"] > 0


def test_setting_survives_a_restart_and_defaults_off(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    store = FileBackedAiSettingsStore(path)
    assert store.get().share_test_source is False
    store.update(share_test_source=True)
    assert FileBackedAiSettingsStore(path).get().share_test_source is True
    assert store.get().to_public_status()["share_test_source"] is True


def test_ci_env_switch() -> None:
    assert settings_from_env({"TESTO_AI_PROVIDER": "openai"}).share_test_source is False  # type: ignore[union-attr]
    on = settings_from_env({"TESTO_AI_PROVIDER": "openai", "TESTO_AI_SHARE_TEST_SOURCE": "1"})
    assert on is not None and on.share_test_source is True


def test_failing_tests_and_traces_come_from_the_allure_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    import json

    from testo_core import paths
    from testo_core.services.failure_source import failing_tests_of

    results = tmp_path / "snap" / "allure-results"
    results.mkdir(parents=True)
    for name, status in (("test_create_order", "failed"), ("test_other", "passed")):
        (results / f"{name}-result.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "fullName": f"tests.test_orders#{name}",
                    "status": status,
                    "statusDetails": {
                        "message": "assert 500 == 201",
                        "trace": "tests/test_orders.py:10: AssertionError",
                    },
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(paths, "ORCHESTRATOR_ROOT", tmp_path)
    run = dataclasses.replace(_failed_run(), snapshot_dir="snap", target_repo=str(repo))
    tests = failing_tests_of(run)
    assert [t.name for t in tests] == ["test_create_order"]
    snippets = collect_sources(tests, repo=repo)
    assert snippets and snippets[0].path == "tests/test_orders.py"
