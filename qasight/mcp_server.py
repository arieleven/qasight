"""``qasight mcp``: a Model Context Protocol server over the run history.

Coding agents (Claude Code, Claude Desktop, Cursor, ...) start it over stdio and ask
what failed, what's flaky and what changed, instead of scraping logs. Every tool is a
thin wrapper over a service the CLI and the API already use (run failures, flaky
detection, test history, Compare, project health, triage), so there is one read path.

The tool functions live on :class:`HistoryTools` and return plain JSON-able dicts with
capped sizes: failure messages are cut to their first line, lists to :data:`MAX_ITEMS`,
and no log or trace is ever returned. They need no MCP SDK; only :func:`build_server`
imports it (the ``mcp`` extra), so ``import qasight`` never does.

Write tools (``mark_failure_cause``) are registered only with ``allow_writes``.
"""

from __future__ import annotations

import functools
import importlib
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from qasight.config.schema import QuarantineRule
    from qasight.history.views import CompletedRunView

SERVER_NAME = "qasight"
# Longest list any tool returns (tests, runs, changes, history points).
MAX_ITEMS = 50
MAX_RUNS = 100
MAX_WINDOW = 100
# Failure messages: first line only, at most this many characters.
MAX_MESSAGE = 300
_FAILED = frozenset({"failed", "broken"})

READ_TOOLS = (
    "list_cycles",
    "list_runs",
    "get_run",
    "failing_tests",
    "flaky_tests",
    "test_history",
    "compare_runs",
    "project_health",
)
WRITE_TOOLS = ("mark_failure_cause",)


class ToolInputError(ValueError):
    """A bad argument or an unknown run, cycle or test; the agent sees the message."""


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat(timespec="seconds") if ts else None


def _short(message: str | None) -> str:
    first = (message or "").strip().splitlines()[0] if (message or "").strip() else ""
    return first if len(first) <= MAX_MESSAGE else first[: MAX_MESSAGE - 1] + "…"


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _outcome(run: CompletedRunView) -> str:
    """``passed`` / ``failed`` the way the run list and project health judge a run."""
    status = getattr(run.status, "value", run.status)
    if status in ("PENDING", "RUNNING"):
        return "running"
    return "failed" if run.returncode != 0 or status == "FAILED" else "passed"


def _run_summary(run: CompletedRunView) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "cycle": run.cycle,
        "outcome": _outcome(run),
        "exit_code": run.returncode,
        "created_at": _iso(run.created_at),
        "duration_s": round(run.wall_duration_ms / 1000.0, 2),
        "branch": run.branch,
        "commit": run.commit,
        "tests": run.total_tests,
        "passed": run.passed,
        "failed": run.failed,
        "broken": run.broken,
        "skipped": run.skipped,
    }


class HistoryTools:
    """The MCP tools, callable directly (tests) or through :func:`build_server`.

    ``quarantine`` maps a cycle to its ``quarantine:`` rules from the config, so
    ``flaky_tests`` can say which flaky tests are already quarantined.
    """

    def __init__(self, quarantine: dict[str, tuple[QuarantineRule, ...]] | None = None) -> None:
        self._quarantine = quarantine or {}

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _require_run(run_id: str) -> CompletedRunView:
        from qasight.history.read_model import get_run

        cleaned = str(run_id or "").strip()
        if not cleaned:
            raise ToolInputError("run_id is required (see list_runs)")
        run = get_run(run_id=cleaned)
        if run is None:
            raise ToolInputError(f"no run {cleaned!r} in the run history (see list_runs)")
        return run

    @staticmethod
    def _latest(cycle: str) -> CompletedRunView:
        from qasight.history.read_model import latest_run_per_cycle

        name = str(cycle or "").strip()
        latest = latest_run_per_cycle()
        if name not in latest:
            known = ", ".join(sorted(latest)[:20]) or "none recorded yet"
            raise ToolInputError(f"no recorded run of cycle {name!r}; cycles: {known}")
        return latest[name]

    @staticmethod
    def _failures(run: CompletedRunView, limit: int = MAX_ITEMS) -> dict[str, Any]:
        from qasight.services.run_failures import load_run_failures

        result = load_run_failures(run.run_id)
        if result is None:  # deleted between the lookup and now
            raise ToolInputError(f"no run {run.run_id!r} in the run history")
        tests = [
            {
                "name": t.name,
                "full_name": t.full_name,
                "group": t.group,
                "stage": t.stage,
                "status": t.status,
                "message": _short(t.message),
                "new": t.new,
                "failing_runs": t.failing_runs,
                "failing_since_run_id": t.failing_since_run_id,
                "flaky": t.flaky,
                "cause": t.triage.cause if t.triage else None,
                "cause_note": t.triage.note if t.triage else None,
            }
            for t in result.tests[:limit]
        ]
        return {
            "failing": len(result.tests),
            "new_failures": result.new_failures,
            "baseline_run_id": result.baseline_run_id,
            "baseline_branch": result.baseline_branch,
            "tests": tests,
            "truncated": len(result.tests) > limit,
            "same_cause_groups": [
                {"message": _short(g.message), "count": g.count} for g in result.groups[:MAX_ITEMS]
            ],
            "rerun_commands": list(result.rerun_commands),
        }

    # -- read tools ------------------------------------------------------------

    def list_cycles(self) -> dict[str, Any]:
        """Every cycle with a recorded run, newest first, with its latest run's outcome and counts."""
        from qasight.history.read_model import latest_run_per_cycle

        cycles = [_run_summary(run) for run in latest_run_per_cycle().values()]
        recorded = {c["cycle"] for c in cycles}
        never_run = sorted(set(self._quarantine) - recorded)
        return {"cycles": cycles[:MAX_RUNS], "configured_never_run": never_run[:MAX_RUNS]}

    def list_runs(self, cycle: str | None = None, limit: int = 20) -> dict[str, Any]:
        """Recent runs, newest first, optionally of one cycle (limit 1-100)."""
        from qasight.history.read_model import list_recent_runs

        limit = _clamp(limit, 1, MAX_RUNS)
        name = (cycle or "").strip()
        runs = list_recent_runs(limit=500 if name else limit)
        if name:
            runs = [r for r in runs if r.cycle == name]
        return {"cycle": name or None, "runs": [_run_summary(r) for r in runs[:limit]]}

    def get_run(self, run_id: str) -> dict[str, Any]:
        """One run: outcome, counts, stages, and its failing tests (new breakages first) with rerun commands."""
        run = self._require_run(run_id)
        stages = [
            {
                "name": s.get("name"),
                "framework": s.get("framework"),
                "returncode": s.get("returncode"),
                "duration_s": s.get("duration_s"),
                "quarantined": list(s.get("quarantined") or [])[:MAX_ITEMS],
            }
            for s in run.stage_health[:MAX_ITEMS]
        ]
        return {
            **_run_summary(run),
            "base_branch": run.base_branch,
            "repository": run.repository,
            "stages": stages,
            "failures": self._failures(run),
        }

    def failing_tests(self, cycle: str) -> dict[str, Any]:
        """The failing tests of a cycle's latest run: message, since when, flaky or new, cause, rerun command."""
        run = self._latest(cycle)
        return {"run": _run_summary(run), **self._failures(run)}

    def flaky_tests(self, cycle: str, window: int = 20) -> dict[str, Any]:
        """Flaky tests in the cycle's last `window` runs (2-100), each with why; broken tests listed apart.

        Flaky: passed on a retry, passed and failed on one commit, or flipped twice on one branch.
        Broken: failed each of its last 3 runs (a real failure, not flakiness).
        """
        from qasight.engine.quarantine import matching_rule
        from qasight.services.flaky_service import find_flaky_tests

        name = str(cycle or "").strip()
        if not name:
            raise ToolInputError("cycle is required (see list_cycles)")
        report = find_flaky_tests(cycle=name, window=_clamp(window, 2, MAX_WINDOW))
        rules = self._quarantine.get(name, ())
        flaky = [
            {
                "name": t.name,
                "full_name": t.full_name,
                "group": t.group,
                "runs": t.runs,
                "passed": t.passed,
                "failed": t.failed,
                "flips": t.flips,
                "flip_rate": round(t.flip_rate, 3),
                "last_status": t.last_status,
                "history": "".join(
                    "P" if s == "passed" else "F" if s in _FAILED else "." for s in t.history
                ),
                "quarantined": bool(rules)
                and matching_rule(rules, name=t.name, full_name=t.full_name) is not None,
                "reasons": list(t.reasons),
            }
            for t in report.flaky[:MAX_ITEMS]
        ]
        broken = [
            {
                "name": b.name,
                "full_name": b.full_name,
                "group": b.group,
                "failing_runs": b.failing_runs,
                "message": _short(b.message),
            }
            for b in report.broken[:MAX_ITEMS]
        ]
        return {
            "cycle": report.cycle,
            "window": report.window,
            "runs_analyzed": report.runs_analyzed,
            "tests_analyzed": report.tests_analyzed,
            "flaky_count": len(report.flaky),
            "flaky": flaky,
            "broken": broken,
            "history_legend": "oldest to newest; P passed, F failed, . skipped or unknown",
        }

    def test_history(self, cycle: str, test: str) -> dict[str, Any]:
        """One test's outcome, duration and message in each of the cycle's last 20 runs, newest first.

        `test` is the test's name, full name or key; a partial name works when it matches one test.
        """
        from qasight.services.test_history import list_cycle_tests, load_test_history

        name, needle = str(cycle or "").strip(), str(test or "").strip()
        if not name or not needle:
            raise ToolInputError("cycle and test are required")
        tests = list_cycle_tests(name, query=needle).tests
        exact = [t for t in tests if needle in (t.key, t.name, t.full_name)]
        matches = exact or list(tests)
        if not matches:
            raise ToolInputError(f"no test matching {needle!r} in cycle {name!r}'s recent runs")
        if len(matches) > 1:
            return {
                "cycle": name,
                "ambiguous": True,
                "candidates": [
                    {"name": t.name, "full_name": t.full_name, "key": t.key}
                    for t in matches[:MAX_ITEMS]
                ],
            }
        history = load_test_history(name, matches[0].key)
        if history is None:
            raise ToolInputError(f"no history for {needle!r} in cycle {name!r}")
        s = history.summary
        return {
            "cycle": name,
            "test": {
                "name": s.name,
                "full_name": s.full_name,
                "key": s.key,
                "group": s.group,
                "stage": s.stage,
                "runs": s.runs,
                "passed": s.passed,
                "failed": s.failed,
                "last_status": s.last_status,
                "flaky": s.flaky,
                "median_duration_ms": s.median_duration_ms,
                "slowdown_ms": s.slowdown_ms,
            },
            "points": [
                {
                    "run_id": p.run_id,
                    "created_at": _iso(p.created_at),
                    "status": p.status,
                    "duration_ms": p.duration_ms,
                    "branch": p.branch,
                    "message": _short(p.message),
                }
                for p in history.points[:MAX_ITEMS]
            ],
        }

    def compare_runs(self, baseline_id: str, current_id: str) -> dict[str, Any]:
        """What changed from `baseline_id` to `current_id`: count and timing deltas, then per-test regressions, fixes, added and removed tests."""
        import tempfile
        from pathlib import Path

        from qasight.services.delta_service import (
            DeltaComparisonError,
            DeltaComparisonService,
        )
        from qasight.services.run_snapshot_diff import diff_run_snapshots

        baseline = self._require_run(baseline_id)
        current = self._require_run(current_id)
        try:
            delta = DeltaComparisonService().compare_runs(
                current_run_id=current.run_id, baseline_run_id=baseline.run_id
            )
        except DeltaComparisonError as exc:
            raise ToolInputError(str(exc)) from exc
        with tempfile.TemporaryDirectory(prefix="testo-mcp-diff-") as td:
            changes = diff_run_snapshots(baseline=baseline, current=current, tmp=Path(td))
        order = {"regression": 0, "fix": 1, "status_change": 2, "added": 3, "removed": 4}
        changes.sort(key=lambda c: (order.get(c.kind, 9), c.name))
        counts: dict[str, int] = {}
        for c in changes:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        return {
            "baseline": _run_summary(baseline),
            "current": _run_summary(current),
            "highlights": list(delta.highlights),
            "metrics": [
                {
                    "metric": m.metric_key,
                    "baseline": m.baseline_value,
                    "current": m.current_value,
                    "delta": m.absolute_delta,
                    "classification": m.classification,
                }
                for m in delta.metrics
            ],
            "test_changes": counts,
            "changes": [
                {
                    "name": c.name,
                    "group": c.group,
                    "kind": c.kind,
                    "baseline_status": c.baseline_status,
                    "current_status": c.current_status,
                    "duration_delta_ms": c.duration_delta_ms,
                }
                for c in changes[:MAX_ITEMS]
            ],
            "truncated": len(changes) > MAX_ITEMS,
        }

    def project_health(self, cycle: str | None = None) -> dict[str, Any]:
        """The project's health score (0-100, graded A-F), its signals, per-cycle health and top findings."""
        from qasight.services.project_health import project_health

        health = project_health(cycle=(cycle or "").strip() or None)
        data = asdict(health)
        data["latest_run_at"] = _iso(health.latest_run_at)
        data["by_cycle"] = [
            {**c, "created_at": _iso(c["created_at"])} for c in data["by_cycle"][:MAX_RUNS]
        ]
        return data

    # -- write tools -----------------------------------------------------------

    def mark_failure_cause(
        self, cycle: str, message: str, cause: str, note: str = ""
    ) -> dict[str, Any]:
        """Record why failures with this message happen in a cycle; later failures like it inherit the cause.

        `cause` is one of product_bug, test_bug, environment, flaky, other. Numbers, ids and
        quoted values in `message` are ignored when matching.
        """
        from qasight.services.triage import CAUSES, TriageError, TriageStore

        if cause not in CAUSES:
            raise ToolInputError(f"cause must be one of {', '.join(CAUSES)}")
        try:
            marked = TriageStore().mark(
                cycle=str(cycle or "").strip(),
                message=str(message or ""),
                cause=cause,
                note=note,
                marked_by="mcp",
            )
        except TriageError as exc:
            raise ToolInputError(str(exc)) from exc
        return {
            "cycle": marked.cycle,
            "cause": marked.cause,
            "cause_label": CAUSES[marked.cause],
            "note": marked.note,
            "signature": marked.signature,
            "marked_at": marked.marked_at.isoformat(timespec="seconds"),
        }


def quarantine_rules(config: Any) -> dict[str, tuple[QuarantineRule, ...]]:
    """``cycle -> quarantine rules`` from a loaded config (every configured cycle is a key)."""
    if config is None:
        return {}
    return {name: tuple(plan.quarantine) for name, plan in config.cycles.items()}


def build_server(tools: HistoryTools, *, allow_writes: bool = False) -> Any:
    """An MCP server with ``tools`` registered; needs the ``mcp`` extra.

    Works with the SDK's 2.x ``MCPServer`` and the 1.x ``FastMCP`` it was renamed from.
    """
    # By name, so type checking passes whichever major version is installed.
    try:  # mcp >= 2
        server_cls: Any = importlib.import_module("mcp.server.mcpserver").MCPServer
        tool_error: Any = importlib.import_module("mcp.server.mcpserver.exceptions").ToolError
    except ImportError:  # mcp 1.x
        server_cls = importlib.import_module("mcp.server.fastmcp").FastMCP
        tool_error = importlib.import_module("mcp.server.fastmcp.exceptions").ToolError
    from mcp.types import ToolAnnotations

    def tool(name: str) -> Callable[..., Any]:
        fn = getattr(tools, name)

        @functools.wraps(fn)
        def call(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except ToolInputError as exc:
                # The SDK's own error type: the agent reads the message, no traceback logged.
                raise tool_error(str(exc)) from exc

        return call

    server = server_cls(
        SERVER_NAME,
        instructions=(
            "This project's QASight test run history: cycles, runs, failing tests with "
            "rerun commands, flaky tests, one test's history, run comparisons and project "
            "health. Start with list_cycles or failing_tests."
            + (
                " mark_failure_cause records why a failure happens."
                if allow_writes
                else " Read-only."
            )
        ),
    )
    read_only = ToolAnnotations.model_validate(
        {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}
    )
    for name in READ_TOOLS:
        server.add_tool(tool(name), name=name, annotations=read_only)
    if allow_writes:
        writes = ToolAnnotations.model_validate(
            {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
        )
        for name in WRITE_TOOLS:
            server.add_tool(tool(name), name=name, annotations=writes)
    return server
