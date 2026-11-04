"""``qasight mcp`` (qasight/mcp_server.py): the tools over a real ingested history."""

from __future__ import annotations

import asyncio
import builtins
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.config.schema import QuarantineRule
from qasight.mcp_server import READ_TOOLS, WRITE_TOOLS, HistoryTools, ToolInputError
from qasight.repository.db import reset_repository_cache
from qasight.repository.db_config import reset_engine_cache

GREEN = """<testsuite name="cart">
<testcase classname="tests.test_cart" name="test_adds" time="0.2"/>
<testcase classname="tests.test_cart" name="test_totals" time="0.1"/>
<testcase classname="tests.test_cart" name="test_flips" time="0.1"/>
</testsuite>"""
RED = """<testsuite name="cart">
<testcase classname="tests.test_cart" name="test_adds" time="0.2"/>
<testcase classname="tests.test_cart" name="test_totals" time="0.3">
<failure message="AssertionError: total 41 != 42">Traceback line 1
line 2</failure></testcase>
<testcase classname="tests.test_cart" name="test_flips" time="0.1">
<failure message="TimeoutError: port 8001">trace</failure></testcase>
</testsuite>"""


@pytest.fixture
def history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Four ingested runs of cycle ``web``: green, red, green, red. Run ids oldest first."""
    for name in ("TESTO_API_TOKEN", "TESTO_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setattr("qasight.paths.ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", tmp_path / "static" / "history")
    monkeypatch.chdir(tmp_path)
    reset_engine_cache()
    reset_repository_cache()
    runner = CliRunner()
    for i, body in enumerate((GREEN, RED, GREEN, RED)):
        report = tmp_path / f"r{i}.xml"
        report.write_text(body, encoding="utf-8")
        result = runner.invoke(app, ["ingest", str(report), "--cycle", "web", "--ci"])
        assert result.exit_code in (0, 1), result.output
        time.sleep(0.01)  # distinct created_at, so "latest" is unambiguous
    ids = [r["run_id"] for r in HistoryTools().list_runs(cycle="web")["runs"]]
    assert len(ids) == 4
    yield list(reversed(ids))
    reset_repository_cache()
    reset_engine_cache()


def test_list_cycles_and_runs(history: list[str]) -> None:
    tools = HistoryTools({"web": (), "nightly": ()})
    cycles = tools.list_cycles()
    assert [c["cycle"] for c in cycles["cycles"]] == ["web"]
    latest = cycles["cycles"][0]
    assert (latest["run_id"], latest["outcome"], latest["failed"]) == (history[-1], "failed", 2)
    assert cycles["configured_never_run"] == ["nightly"]

    runs = tools.list_runs(cycle="web", limit=2)["runs"]
    assert [r["run_id"] for r in runs] == [history[3], history[2]]
    assert [r["outcome"] for r in runs] == ["failed", "passed"]
    assert tools.list_runs(cycle="nope")["runs"] == []


def test_failing_tests_have_short_messages_and_rerun_commands(history: list[str]) -> None:
    result = HistoryTools().failing_tests("web")
    assert result["run"]["run_id"] == history[-1]
    assert result["failing"] == 2
    by_name = {t["name"]: t for t in result["tests"]}
    assert set(by_name) == {"test_totals", "test_flips"}
    assert by_name["test_totals"]["message"] == "AssertionError: total 41 != 42"
    assert "Traceback" not in str(result)  # never the trace or log
    assert by_name["test_flips"]["flaky"] is True
    assert any("test_totals" in cmd for cmd in result["rerun_commands"])


def test_get_run_matches_failing_tests_and_rejects_unknown_ids(history: list[str]) -> None:
    tools = HistoryTools()
    run = tools.get_run(history[1])
    assert run["run_id"] == history[1]
    assert run["outcome"] == "failed"
    assert run["stages"] and run["stages"][0]["name"]
    assert run["failures"]["failing"] == 2
    assert tools.get_run(history[0])["failures"]["tests"] == []
    with pytest.raises(ToolInputError, match="no run"):
        tools.get_run("does-not-exist")
    with pytest.raises(ToolInputError, match="no recorded run of cycle"):
        tools.failing_tests("nope")


def test_flaky_tests_marks_quarantined(history: list[str]) -> None:
    rules = (QuarantineRule(test="test_flip*", reason="known"),)
    result = HistoryTools({"web": rules}).flaky_tests("web", window=20)
    assert result["runs_analyzed"] == 4
    names = {t["name"]: t for t in result["flaky"]}
    assert set(names) == {"test_totals", "test_flips"}
    assert names["test_flips"]["history"] == "PFPF"
    assert names["test_flips"]["quarantined"] is True
    assert names["test_totals"]["quarantined"] is False


def test_test_history_by_name_and_ambiguity(history: list[str]) -> None:
    tools = HistoryTools()
    result = tools.test_history("web", "test_totals")
    assert result["test"]["name"] == "test_totals"
    assert [p["status"] for p in result["points"]] == ["failed", "passed", "failed", "passed"]
    assert result["points"][0]["run_id"] == history[-1]
    assert tools.test_history("web", "test_")["ambiguous"] is True
    with pytest.raises(ToolInputError, match="no test matching"):
        tools.test_history("web", "missing")


def test_compare_runs_lists_regressions_first(history: list[str]) -> None:
    result = HistoryTools().compare_runs(history[2], history[3])
    assert result["test_changes"] == {"regression": 2}
    assert [c["kind"] for c in result["changes"]] == ["regression", "regression"]
    assert {m["metric"] for m in result["metrics"]} >= {"failed", "passed"}
    with pytest.raises(ToolInputError):
        HistoryTools().compare_runs(history[0], "nope")


def test_project_health(history: list[str]) -> None:
    health = HistoryTools().project_health()
    assert health["cycles"] == 1
    assert health["by_cycle"][0]["cycle"] == "web"
    assert isinstance(health["by_cycle"][0]["created_at"], str)
    assert HistoryTools().project_health("nope")["cycles"] == 0


def test_mark_failure_cause_labels_later_failures(history: list[str]) -> None:
    tools = HistoryTools()
    marked = tools.mark_failure_cause(
        "web", "AssertionError: total 7 != 8", "product_bug", note="cart rounding"
    )
    assert marked["cause"] == "product_bug"
    by_name = {t["name"]: t for t in tools.failing_tests("web")["tests"]}
    assert by_name["test_totals"]["cause"] == "product_bug"
    assert by_name["test_totals"]["cause_note"] == "cart rounding"
    with pytest.raises(ToolInputError, match="cause must be one of"):
        tools.mark_failure_cause("web", "x", "bogus")


def test_cli_without_the_extra_says_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def no_mcp(name: str, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if name == "mcp" or name.startswith("mcp."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_mcp)
    result = CliRunner().invoke(app, ["mcp"])
    assert result.exit_code == 2
    assert "The MCP server needs the mcp extra: pip install 'qasight[mcp]'" in result.output


def test_server_registers_write_tools_only_when_allowed() -> None:
    pytest.importorskip("mcp")
    from qasight.mcp_server import build_server

    def names(allow_writes: bool) -> set[str]:
        server = build_server(HistoryTools(), allow_writes=allow_writes)
        return {t.name for t in asyncio.run(server.list_tools())}

    assert names(False) == set(READ_TOOLS)
    assert names(True) == set(READ_TOOLS) | set(WRITE_TOOLS)


def test_server_calls_a_tool_over_the_sdk(history: list[str]) -> None:
    pytest.importorskip("mcp")
    from qasight.mcp_server import build_server

    server = build_server(HistoryTools())
    result = asyncio.run(server.call_tool("list_runs", {"cycle": "web", "limit": 1}))
    assert history[-1] in str(result)
