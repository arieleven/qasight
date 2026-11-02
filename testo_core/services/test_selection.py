"""Run a chosen set of tests: ``testo run --test NAME`` and ``--rerun-failed RUN_ID``.

A selection is a list of tests by name (and, when known, the stage they ran
in). :func:`narrow_plan` turns it into the same cycle with each stage told to run
only those tests, in the runner's own words:

    pytest              node ids when every name points at a module and the stage
                        names no path of its own, else ``-k`` on the names
    Playwright          ``--grep`` on the titles, plus ``--pass-with-no-tests``
    Behave / BehaveX    ``--name`` on the scenario names

Every stage also gets Allure's public test plan file (``ALLURE_TESTPLAN_PATH``),
which Allure's own adapters (JUnit 5, TestNG, Cucumber, allure-pytest...) read to
run just those tests, and ``TESTO_SELECTED_TESTS`` with the names one per line, for
a command stage's own script. A stage none of whose tests was chosen is skipped
when the selection names stages (``--rerun-failed``); with bare names every stage
runs, narrowed, and a pytest stage that collects none of them counts as passed.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path

from testo_core.config.schema import Plan, Stage
from testo_core.services.rerun_commands import pytest_node_id

ENV_TESTPLAN = "ALLURE_TESTPLAN_PATH"
ENV_SELECTED = "TESTO_SELECTED_TESTS"
METADATA_KEY = "selected_tests"
MAX_TESTS = 500
_REGEX_SPECIAL = re.compile(r"[.*+?^${}()|\[\]\\/]")


class SelectionError(ValueError):
    """The selection is empty, too large, or names a run that has nothing to rerun."""


@dataclass(frozen=True)
class SelectedTest:
    name: str
    full_name: str = ""
    group: str = ""
    # The stage it ran in, when the selection comes from a recorded run.
    stage: str = ""


@dataclass(frozen=True)
class Selection:
    tests: tuple[SelectedTest, ...]
    # Where it came from, for the run record and the log: "--test", "failures of run 1a2b".
    source: str = ""

    @property
    def stages(self) -> frozenset[str]:
        return frozenset(t.stage for t in self.tests if t.stage)


def from_names(names: list[str]) -> Selection:
    """``--test`` values: a test's name, full name or pytest node id."""
    cleaned = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    if not cleaned:
        raise SelectionError("no test names given.")
    if len(cleaned) > MAX_TESTS:
        raise SelectionError(f"too many tests ({len(cleaned)}; the limit is {MAX_TESTS}).")
    tests = tuple(
        SelectedTest(name=n.rsplit("::", 1)[-1], full_name=n if ("::" in n or "." in n) else "")
        for n in cleaned
    )
    return Selection(tests=tests, source="--test")


def from_run_failures(run_id: str) -> Selection:
    """The failing tests of a recorded run, each with the stage it failed in."""
    from testo_core.services.run_failures import load_run_failures

    failures = load_run_failures(run_id)
    if failures is None:
        raise SelectionError(f"no run {run_id!r} in the history.")
    if not failures.tests:
        raise SelectionError(f"run {run_id} has no failing tests to rerun.")
    tests = tuple(
        SelectedTest(name=t.name, full_name=t.full_name, group=t.group, stage=t.stage)
        for t in failures.tests[:MAX_TESTS]
    )
    return Selection(tests=tests, source=f"failures of run {failures.run_id}")


def _alternation(names: list[str]) -> str:
    return "|".join(_REGEX_SPECIAL.sub(lambda m: "\\" + m.group(0), n) for n in names)


def _has_paths(args: tuple[str, ...]) -> bool:
    """Whether a stage's own pytest arguments name paths (``tests/``), which node ids can't narrow."""
    return any(not a.startswith("-") for a in args)


def _pytest_args(stage: Stage, tests: list[SelectedTest]) -> tuple[str, ...]:
    ids = [pytest_node_id(t.full_name, t.name, t.group) if t.full_name else None for t in tests]
    # Node ids next to a path the stage already names would run that whole path too.
    if ids and all(i is not None for i in ids) and not _has_paths(stage.args):
        return tuple(dict.fromkeys(i for i in ids if i))
    # -k takes Python-identifier-ish words; brackets of parametrized ids don't parse.
    words = [re.sub(r"\[.*$", "", t.name) for t in tests]
    return ("-k", " or ".join(dict.fromkeys(w for w in words if w)))


def stage_args(stage: Stage, tests: list[SelectedTest]) -> tuple[str, ...]:
    """What to append to ``stage``'s own arguments to run only ``tests``."""
    names = list(dict.fromkeys(t.name for t in tests))
    if stage.framework == "pytest":
        return _pytest_args(stage, tests)
    if stage.framework == "playwright":
        return ("--grep", _alternation(names), "--pass-with-no-tests")
    if stage.framework in ("behave", "behavex"):
        return ("--name", f"^({_alternation(names)})$")
    return ()


def testplan(tests: list[SelectedTest]) -> dict[str, object]:
    """Allure's test plan file: ``{"version": "1.0", "tests": [{"selector": ...}]}``."""
    return {
        "version": "1.0",
        "tests": [{"selector": t.full_name or t.name} for t in tests],
    }


def narrow_plan(plan: Plan, selection: Selection, *, scratch: Path) -> Plan:
    """``plan`` with each stage running only the selected tests.

    ``scratch`` is where the test plan file is written (one per stage).
    """
    named_stages = selection.stages
    stages: list[Stage] = []
    for stage in plan.stages:
        if named_stages and stage.name not in named_stages:
            continue
        tests = [t for t in selection.tests if not t.stage or t.stage == stage.name]
        if not tests:
            continue
        scratch.mkdir(parents=True, exist_ok=True)
        plan_file = scratch / f"testplan-{re.sub(r'[^A-Za-z0-9_.-]+', '-', stage.name)}.json"
        plan_file.write_text(json.dumps(testplan(tests), indent=2), encoding="utf-8")
        env = dict(stage.extra_env)
        env[ENV_TESTPLAN] = str(plan_file.resolve())
        env[ENV_SELECTED] = "\n".join(dict.fromkeys(t.full_name or t.name for t in tests))
        stages.append(
            replace(
                stage,
                args=(*stage.args, *stage_args(stage, tests)),
                extra_env=tuple(env.items()),
            )
        )
    if not stages:
        raise SelectionError(
            "none of the chosen tests belongs to a stage of this cycle "
            f"({', '.join(sorted(named_stages))} not in {plan.name!r})."
        )
    return replace(plan, stages=tuple(stages))


def describe(plan: Plan) -> list[str]:
    """One line per narrowed stage, for the CLI: ``api: pytest tests/a.py::test_x``."""
    lines = []
    for stage in plan.stages:
        env = dict(stage.extra_env)
        if ENV_SELECTED in env:
            count = len(env[ENV_SELECTED].splitlines())
            lines.append(f"{stage.name}: {count} test(s), {shlex.join(stage.args) or '(no args)'}")
    return lines
