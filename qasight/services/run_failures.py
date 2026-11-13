"""The failing tests of one run, for the run page: what failed, since when, and why.

Per-test outcomes come from each run's artifact snapshot (the Allure
``*-result.json`` files), the same source Compare and flaky detection read, so
this needs no schema change and works for engine and ingested runs alike.

For each failing test it reports whether it is new against the run's baseline
(the latest run on a pull request's target branch, else the run before on the
same branch, as in the PR comment), how many runs in a row it has been failing
on this branch (``failing_runs``), whether it is flaky in the cycle's recent
history, and the first line of its failure message. Tests that
fail with the same message are grouped, so ten tests that all hit
``ConnectionRefusedError`` read as one cause, not ten. A cause someone marked
for a message (:mod:`~qasight.services.triage`) rides along on each test
that fails with it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qasight.history.cases import cases_for_run
from qasight.history.read_model import get_run, list_cycle_runs
from qasight.history.views import CompletedRunView
from qasight.services import flaky_service
from qasight.services.rerun_commands import FailingCase, rerun_commands

if TYPE_CHECKING:
    from qasight.services.triage import Triage

logger = logging.getLogger(__name__)

# Runs of the cycle (this one included) looked at for "failing since" and flakiness.
DEFAULT_WINDOW = flaky_service.DEFAULT_WINDOW
_FAILED = frozenset({"failed", "broken"})
# Numbers, hex ids and quoted values vary between otherwise identical failures.
_VOLATILE = re.compile(r"0x[0-9a-fA-F]+|\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"")


@dataclass(frozen=True)
class FailingTest:
    key: str
    name: str
    full_name: str
    group: str
    stage: str
    status: str
    message: str
    duration_ms: int | None
    # Consecutive runs of the cycle on this run's branch, ending with this one, in which it failed.
    failing_runs: int
    # The oldest run of that streak (this run when ``failing_runs`` is 1).
    failing_since_run_id: str
    flaky: bool
    # Not failing on the baseline run (or, with no baseline, not in the run before).
    new: bool
    # The cause someone marked for this message in this cycle (services/triage.py).
    triage: Triage | None = None
    # With no cause marked: what a well-known message points to (services/cause_hints.py).
    suggested_cause: str | None = None
    # "stack_overflow" | "crash" when its message or trace shows one (engine/crash_signatures.py).
    crash_kind: str | None = None
    # What the test is about ({name, value}: feature, severity, tag...) and its issue links.
    labels: tuple[dict[str, str], ...] = ()
    links: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class FailureGroup:
    message: str
    count: int
    keys: tuple[str, ...]


@dataclass(frozen=True)
class RunFailures:
    run_id: str
    runs_considered: int
    tests: tuple[FailingTest, ...]
    # Messages shared by two or more failing tests, largest group first.
    groups: tuple[FailureGroup, ...]
    baseline_run_id: str | None = None
    baseline_branch: str | None = None
    # Commands that rerun just these tests, one per runner that can name them.
    rerun_commands: tuple[str, ...] = ()

    @property
    def new_failures(self) -> int:
        return sum(1 for t in self.tests if t.new)


def message_signature(message: str) -> str:
    """``message`` with the parts that vary between runs (numbers, ids, quoted values) blanked."""
    return _VOLATILE.sub("…", message).strip().lower()


def _cases(run: CompletedRunView) -> dict[str, dict[str, Any]]:
    return cases_for_run(run)


def _earlier_runs(run: CompletedRunView) -> list[CompletedRunView]:
    """``run``'s cycle's runs before it, on any branch, newest first."""
    if not run.cycle:
        return []
    return list_cycle_runs(run.cycle, before_run_id=run.run_id)


def _baseline(
    run: CompletedRunView, earlier: list[CompletedRunView], lineage: list[CompletedRunView]
) -> CompletedRunView | None:
    """A pull request's target branch's latest run, else the run before on the same branch."""
    target = run.base_branch
    if target and target != run.branch:
        on_target = [r for r in earlier if r.branch == target]
        # The target branch's latest run on the same environment, when there is one.
        same_env = next((r for r in on_target if r.environment == run.environment), None)
        if same_env is not None or on_target:
            return same_env or on_target[0]
    return lineage[0] if lineage else None


def _groups(tests: list[FailingTest]) -> tuple[FailureGroup, ...]:
    by_sig: dict[str, list[FailingTest]] = {}
    for test in tests:
        if test.message:
            by_sig.setdefault(message_signature(test.message), []).append(test)
    groups = [
        FailureGroup(
            message=members[0].message, count=len(members), keys=tuple(t.key for t in members)
        )
        for members in by_sig.values()
        if len(members) > 1
    ]
    groups.sort(key=lambda g: (-g.count, g.message))
    return tuple(groups)


def _with_triage(cycle: str | None, tests: list[FailingTest]) -> list[FailingTest]:
    """``tests`` with the causes marked for their messages; unchanged if the store can't be read."""
    if not cycle:
        return tests
    from qasight.services.triage import TriageStore

    try:
        causes = TriageStore().for_messages(cycle, {t.message for t in tests})
    except Exception:
        # Broad on purpose: a missing cause must never hide the failing tests themselves.
        logger.warning("could not read failure causes", exc_info=True)
        return tests
    return [replace(t, triage=causes[t.message]) if t.message in causes else t for t in tests]


def _with_suggestions(tests: list[FailingTest]) -> list[FailingTest]:
    """``tests`` with a suggested cause on each failure nobody has marked yet."""
    from qasight.services.cause_hints import suggest_cause

    return [
        replace(t, suggested_cause=hint)
        if t.triage is None and (hint := suggest_cause(t.message))
        else t
        for t in tests
    ]


def load_run_failures(run_id: str, *, window: int = DEFAULT_WINDOW) -> RunFailures | None:
    """``run_id``'s failing tests, newest breakages first; ``None`` if the run doesn't exist."""
    run = get_run(run_id=run_id)
    if run is None:
        return None
    window = max(2, int(window))
    current = _cases(run)
    failing = {k: c for k, c in current.items() if c["status"] in _FAILED}
    if not failing:
        return RunFailures(run_id=run.run_id, runs_considered=1, tests=(), groups=())

    earlier = _earlier_runs(run)
    # Streaks and flakiness follow this run's branch, so another branch's runs don't mix in.
    # Runs that recorded no branch can't be told apart, so they stay in.
    lineage = [r for r in earlier if r.branch in (run.branch, None)] if run.branch else earlier
    # The same for the environment: a firefox run's streak counts firefox runs only.
    if run.environment:
        lineage = [r for r in lineage if r.environment in (run.environment, None)]
    earlier_cases = [(r, _cases(r)) for r in lineage[: window - 1]]
    earlier_cases = [(r, c) for r, c in earlier_cases if c]
    baseline = _baseline(run, earlier, lineage)
    baseline_cases = _cases(baseline) if baseline is not None else {}

    # Flaky over the same window, ending at this run (oldest first for flaky_service).
    observations: dict[str, list[dict[str, Any]]] = {}
    # The same observations as the flaky page (branch, commit, environment), so the
    # "flaky" tag here agrees with it.
    for older, cases in reversed(earlier_cases):
        for key in failing:
            if key in cases:
                observations.setdefault(key, []).append(
                    flaky_service.observation(older, cases[key])
                )
    for key, case in failing.items():
        observations.setdefault(key, []).append(flaky_service.observation(run, case))

    tests: list[FailingTest] = []
    for key, case in failing.items():
        streak, since = 1, run.run_id
        for older, cases in earlier_cases:
            prev = cases.get(key)
            if prev is None or prev["status"] not in _FAILED:
                break
            streak, since = streak + 1, older.run_id
        if baseline_cases:
            on_baseline = baseline_cases.get(key)
            new = on_baseline is None or on_baseline["status"] not in _FAILED
        else:
            new = streak == 1
        tests.append(
            FailingTest(
                key=key[:200],
                name=str(case["name"]),
                full_name=str(case.get("full_name") or ""),
                group=str(case.get("group") or ""),
                stage=str(case.get("stage") or ""),
                status=str(case["status"]),
                message=str(case.get("message") or ""),
                duration_ms=case.get("duration_ms"),
                failing_runs=streak,
                failing_since_run_id=since,
                flaky=flaky_service.classify(key, observations.get(key, [])) is not None,
                new=new,
                crash_kind=str(case.get("crash") or "") or None,
                labels=tuple(case.get("labels") or ()),
                links=tuple(case.get("links") or ()),
            )
        )
    tests = _with_suggestions(_with_triage(run.cycle, tests))
    case_frameworks = {k[:200]: str(c.get("framework") or "") for k, c in failing.items()}
    # New breakages first, then the longest-standing ones; ties by stage and name.
    tests.sort(key=lambda t: (not t.new, -t.failing_runs, t.stage, t.name))
    return RunFailures(
        run_id=run.run_id,
        runs_considered=1 + len(earlier_cases),
        tests=tuple(tests),
        groups=_groups(tests),
        rerun_commands=tuple(
            rerun_commands(
                [
                    FailingCase(
                        t.name, t.full_name, t.group, t.stage, case_frameworks.get(t.key, "")
                    )
                    for t in tests
                ],
                {str(st.get("name")): str(st.get("framework") or "") for st in run.stage_health},
                repo=Path(run.target_repo) if run.target_repo else None,
            )
        ),
        baseline_run_id=baseline.run_id if baseline_cases and baseline else None,
        # Named only when it is another branch (a PR's target), so the UI can say "new since main".
        baseline_branch=(
            baseline.branch
            if baseline_cases and baseline and baseline.branch != run.branch
            else None
        ),
    )
