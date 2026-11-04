"""Builders and adapters shared by the modern engine/CLI test suite.

Documented in ``docs/Testing Workflows/QA Strategies.md`` (§Testing the
orchestrator itself).  Import as ``from tests.fixtures.engine import ...`` —
``pythonpath = .`` in ``pytest.ini`` makes the repo root importable.

The core trick: real ``qasight.yaml`` configs declare stages with a
supported framework name (``pytest``), and tests monkeypatch
``qasight.engine.executor.get_adapter`` so every stage shells out to
``scripts/echo.py`` instead — a real subprocess whose output/exit code is
scripted entirely by the stage ``args``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
ECHO_SCRIPT = SCRIPTS_DIR / "echo.py"
HANG_SCRIPT = SCRIPTS_DIR / "hang.py"


# ---------------------------------------------------------------------------
# Framework adapters
# ---------------------------------------------------------------------------
class EchoAdapter:
    """Adapter that launches ``scripts/echo.py`` with the stage's args.

    Every ``build_argv`` call is recorded in :attr:`calls` so tests can assert
    on the workers/results_dir the executor resolved.
    """

    name = "echo"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def results_subdir(self) -> str:
        return "echo"

    def build_argv(
        self,
        *,
        target_repo: Path,
        results_dir: Path,
        stage_args: tuple[str, ...],
        workers: int,
    ) -> list[str]:
        self.calls.append(
            {
                "target_repo": Path(target_repo),
                "results_dir": Path(results_dir),
                "stage_args": tuple(stage_args),
                "workers": int(workers),
            }
        )
        return [sys.executable, str(ECHO_SCRIPT), *stage_args]


class MissingBinaryAdapter:
    """Adapter whose argv points at a binary that cannot exist (EC-03a → rc 127)."""

    name = "missing"

    def results_subdir(self) -> str:
        return "missing"

    def build_argv(self, **_: Any) -> list[str]:
        return ["testo-missing-binary-9c2f4e"]


class HangAdapter:
    """Adapter that launches ``scripts/hang.py``; pair with a tiny ``timeout_s`` (EC-03b)."""

    name = "hang"

    def results_subdir(self) -> str:
        return "hang"

    def build_argv(self, **_: Any) -> list[str]:
        return [sys.executable, str(HANG_SCRIPT)]


def use_adapter(monkeypatch: pytest.MonkeyPatch, adapter: Any) -> Any:
    """Route every framework name to ``adapter`` for the duration of the test."""
    from qasight.engine import executor

    monkeypatch.setattr(executor, "get_adapter", lambda framework: adapter)
    return adapter


def use_echo_adapter(monkeypatch: pytest.MonkeyPatch) -> EchoAdapter:
    return use_adapter(monkeypatch, EchoAdapter())


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
class NoopRenderer:
    """Event sink that satisfies the orchestrator protocol and does nothing."""

    wants_streaming = False

    def handle(self, event: Any) -> None:  # noqa: ARG002
        return None


# ---------------------------------------------------------------------------
# NDJSON helpers
# ---------------------------------------------------------------------------
def parse_ndjson(text: str) -> list[dict[str, Any]]:
    """Parse NDJSON output strictly: every non-empty line must be a JSON object."""
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        assert isinstance(payload, dict), f"NDJSON line is not an object: {line!r}"
        events.append(payload)
    return events


def assert_ndjson_events(
    events: Sequence[Mapping[str, Any]], expected_kinds: Iterable[str]
) -> None:
    """Assert the exact ``event`` field sequence of an NDJSON stream."""
    kinds = [e["event"] for e in events]
    assert kinds == list(expected_kinds), f"NDJSON event order mismatch: {kinds}"


def read_artifact_events(artifacts_root: Path, plan_name: str) -> list[dict[str, Any]]:
    """Load ``artifacts/<plan>/events.ndjson`` (the artifact mirror)."""
    return parse_ndjson((artifacts_root / plan_name / "events.ndjson").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# qasight.yaml builders
# ---------------------------------------------------------------------------
def write_config(root: Path, payload: Mapping[str, Any]) -> Path:
    """Dump ``payload`` to ``<root>/qasight.yaml`` and return the path."""
    path = root / "qasight.yaml"
    path.write_text(yaml.safe_dump(dict(payload), sort_keys=False), encoding="utf-8")
    return path


def stage_spec(
    name: str,
    *,
    args: Sequence[str] = (),
    timeout_s: float | None = None,
    extra_env: Mapping[str, str] | None = None,
    workers: int | None = None,
) -> dict[str, Any]:
    """One YAML stage entry.  ``equipment`` must be a supported framework name;
    the adapter is monkeypatched to :class:`EchoAdapter` in tests."""
    spec: dict[str, Any] = {"name": name, "equipment": "pytest", "args": list(args)}
    if timeout_s is not None:
        spec["timeout_s"] = timeout_s
    if extra_env is not None:
        spec["extra_env"] = dict(extra_env)
    if workers is not None:
        spec["workers"] = workers
    return spec


def write_minimal_config(
    root: Path,
    *,
    cycle: str = "smoke",
    args: Sequence[str] = ("--text", "smoke-ok"),
    timeout_s: float | None = None,
) -> Path:
    """Single cycle, single echo stage."""
    return write_config(
        root,
        {
            "version": 1,
            "defaults": {"target_repo": ".", "artifacts_root": "artifacts"},
            "cycles": {
                cycle: {"stages": [stage_spec(f"{cycle}-stage", args=args, timeout_s=timeout_s)]}
            },
        },
    )


def write_multi_stage_config(
    root: Path,
    *,
    cycle: str = "multi",
    stages: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """Single cycle with an explicit stage list (use :func:`stage_spec`)."""
    return write_config(
        root,
        {
            "version": 1,
            "defaults": {"target_repo": ".", "artifacts_root": "artifacts"},
            "cycles": {cycle: {"stages": [dict(s) for s in stages]}},
        },
    )


def write_cycles_config(
    root: Path,
    *,
    cycles: Mapping[str, Sequence[Mapping[str, Any]]],
    trigger_paths: Mapping[str, Sequence[str]] | None = None,
) -> Path:
    """Multiple cycles (for ``--cycle all`` / trigger tests).

    ``trigger_paths`` optionally attaches a ``trigger.paths`` list per cycle.
    """
    cycles_yaml: dict[str, Any] = {}
    for name, stages in cycles.items():
        entry: dict[str, Any] = {"stages": [dict(s) for s in stages]}
        if trigger_paths and name in trigger_paths:
            entry["trigger"] = {"paths": list(trigger_paths[name])}
        cycles_yaml[name] = entry
    return write_config(
        root,
        {
            "version": 1,
            "defaults": {"target_repo": ".", "artifacts_root": "artifacts"},
            "cycles": cycles_yaml,
        },
    )
