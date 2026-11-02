"""Flaky test detection over a cycle's run history.

A test is flaky, across the last ``window`` runs of a cycle, when any of these hold:

* **Passed on retry.** It failed and then passed inside one run (JUnit reruns,
  Surefire ``flakyFailure``, pytest-rerunfailures; ``statusDetails.flaky`` in the
  run's results). The code didn't change between attempts, so the test is unreliable.
* **Same commit, both outcomes.** It passed in one run and failed in another run
  of the same commit.
* **Flips on one branch.** On one branch it both passed and failed and its
  outcome flipped at least twice. Flips are counted per branch, so a pull request
  that broke a test and then fixed it, between passing runs on ``main``, is not
  flaky. One flip is a plain regression or fix, which Compare already shows.

A test that failed in each of its last :data:`BROKEN_STREAK` runs on its latest
branch is **broken**, not flaky: it is listed in :attr:`FlakyReport.broken`
instead, because quarantining or retrying it would hide a real failure.

Per-test outcomes come from the ``test_result`` rows each run stores
(:mod:`testo_core.history.cases`; older runs are read from their artifact
snapshot once and stored), so this works on every database backend. The CLI (``testo flaky``) and the API (``/analytics/flaky``) both call
:func:`find_flaky_tests`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from testo_core.history.cases import cases_for_run
from testo_core.history.read_model import list_cycle_runs
from testo_core.history.views import CompletedRunView

DEFAULT_WINDOW = 20
MIN_FLIPS = 2
# Failing this many runs in a row (on the test's latest branch) means broken, not flaky.
BROKEN_STREAK = 3
# How many recent runs (any cycle) to scan when looking for one cycle's window.

_PASS = "passed"
_FAIL = frozenset({"failed", "broken"})


@dataclass(frozen=True)
class FlakyTest:
    key: str
    name: str
    group: str
    runs: int
    passed: int
    failed: int
    flips: int
    flip_rate: float
    last_status: str
    # Oldest first, one of "passed" / "failed" / "broken" / "skipped" / "unknown"
    # per run the test appeared in.
    history: tuple[str, ...]
    full_name: str = ""
    # Matches a ``quarantine:`` rule of the cycle (set by callers that load the config).
    quarantined: bool = False
    # Why it counts as flaky, in plain words (see the module docstring).
    reasons: tuple[str, ...] = ()
    # Runs in which it failed and then passed on a retry.
    retried: int = 0


@dataclass(frozen=True)
class BrokenTest:
    """Fails every time lately: a real failure to fix, not noise to quarantine."""

    key: str
    name: str
    group: str
    # Runs in a row it has failed, ending at the latest run.
    failing_runs: int
    message: str = ""
    full_name: str = ""


@dataclass(frozen=True)
class FlakyReport:
    cycle: str
    window: int
    runs_analyzed: int
    tests_analyzed: int
    flaky: tuple[FlakyTest, ...]
    broken: tuple[BrokenTest, ...] = ()


def _outcomes(statuses: list[str]) -> list[str]:
    """Pass/fail outcomes only; skipped and unknown runs neither pass nor fail."""
    return ["fail" if s in _FAIL else "pass" for s in statuses if s == _PASS or s in _FAIL]


def observation(run: CompletedRunView, case: dict[str, Any]) -> dict[str, Any]:
    """``case`` plus the run's branch, commit and environment, which :func:`classify` groups by."""
    return {**case, "branch": run.branch, "commit": run.commit, "environment": run.environment}


def _lane(o: dict[str, Any]) -> str:
    """Branch, plus the environment when the run recorded one: "main (browser=firefox)"."""
    branch = str(o.get("branch") or "")
    env = str(o.get("environment") or "")
    return f"{branch} ({env})".strip() if env else branch


def _lanes(observations: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Pass/fail outcomes per branch and environment, oldest first.

    Runs without a branch share one lane, and so do runs without an environment.
    Keeping environments apart means a test that always fails on one browser and
    passes on another doesn't read as flipping.
    """
    lanes: dict[str, list[str]] = {}
    for o in observations:
        outcome = _outcomes([str(o["status"])])
        if outcome:
            lanes.setdefault(_lane(o), []).append(outcome[0])
    return lanes


def _flips(outcomes: list[str]) -> int:
    return sum(1 for a, b in zip(outcomes, outcomes[1:], strict=False) if a != b)


def failing_streak(observations: list[dict[str, Any]]) -> int:
    """Runs in a row the test failed, ending at its latest run, on that run's branch and environment."""
    if not observations:
        return 0
    lane = _lane(observations[-1])
    streak = 0
    for o in reversed(observations):
        if _lane(o) != lane:
            continue
        status = str(o["status"])
        if status in _FAIL:
            streak += 1
        elif status == _PASS:
            break
    return streak


def _same_commit_conflicts(observations: list[dict[str, Any]]) -> int:
    """Commits on which the test both passed and failed (in different runs)."""
    seen: dict[str, set[str]] = {}
    for o in observations:
        commit = str(o.get("commit") or "")
        outcome = _outcomes([str(o["status"])])
        if commit and outcome:
            # Another environment on the same commit is another lane, not a conflict.
            seen.setdefault(f"{commit} @ {o.get('environment') or ''}", set()).add(outcome[0])
    return sum(1 for outcomes in seen.values() if len(outcomes) == 2)


def classify(key: str, observations: list[dict[str, Any]]) -> FlakyTest | None:
    """Score one test from its per-run observations (oldest first); ``None`` if not flaky.

    Observations carry ``branch`` / ``commit`` when built with :func:`observation`;
    without them every run counts as one branch and no commit is shared.
    """
    if not observations or failing_streak(observations) >= BROKEN_STREAK:
        return None
    statuses = [str(o["status"]) for o in observations]
    outcomes = _outcomes(statuses)
    passed = outcomes.count("pass")
    failed = outcomes.count("fail")
    lanes = _lanes(observations)
    flips = sum(_flips(lane) for lane in lanes.values())
    retried = sum(1 for o in observations if o.get("retried") and str(o["status"]) == _PASS)
    conflicts = _same_commit_conflicts(observations)

    reasons: list[str] = []
    if retried:
        reasons.append(f"passed on retry in {retried} run{'s' if retried != 1 else ''}")
    if conflicts:
        reasons.append(
            f"passed and failed on the same commit ({conflicts} commit"
            f"{'s' if conflicts != 1 else ''})"
        )
    flipping = [b for b, lane in lanes.items() if _flips(lane) >= MIN_FLIPS]
    if passed and failed and flipping:
        where = ", ".join(f"on {b}" for b in flipping if b) or "across runs"
        reasons.append(f"flipped {flips} times {where}")
    if not reasons:
        return None
    latest = observations[-1]
    steps = max(1, len(outcomes) - len(lanes))
    return FlakyTest(
        key=key[:80],
        name=str(latest.get("name") or key)[:120],
        group=str(latest.get("group") or "(ungrouped)")[:100],
        runs=len(outcomes),
        passed=passed,
        failed=failed,
        flips=flips,
        flip_rate=round(min(1.0, flips / steps), 3),
        last_status=statuses[-1],
        history=tuple(statuses),
        full_name=str(latest.get("full_name") or ""),
        reasons=tuple(reasons),
        retried=retried,
    )


def broken(key: str, observations: list[dict[str, Any]]) -> BrokenTest | None:
    """The test as broken when it failed its last :data:`BROKEN_STREAK` runs, else ``None``."""
    streak = failing_streak(observations)
    if streak < BROKEN_STREAK:
        return None
    latest = observations[-1]
    return BrokenTest(
        key=key[:80],
        name=str(latest.get("name") or key)[:120],
        group=str(latest.get("group") or "(ungrouped)")[:100],
        failing_runs=streak,
        message=str(latest.get("message") or ""),
        full_name=str(latest.get("full_name") or ""),
    )


def _cases_for_run(run: CompletedRunView) -> dict[str, dict[str, Any]]:
    return cases_for_run(run)


def _cycle_runs(cycle: str, *, limit: int | None = None) -> list[CompletedRunView]:
    """``cycle``'s recorded runs, newest first (at most ``limit``)."""
    return list_cycle_runs(cycle) if limit is None else list_cycle_runs(cycle, limit=limit)


def _report(cycle: str, runs: list[CompletedRunView], window: int) -> FlakyReport:
    """Score the tests in ``runs`` (newest first, already cut to the window)."""
    per_test: dict[str, list[dict[str, Any]]] = {}
    analyzed = 0
    for run in reversed(runs):  # oldest first, so flips are counted in order
        cases = _cases_for_run(run)
        if not cases:
            continue
        analyzed += 1
        for key, case in cases.items():
            per_test.setdefault(key, []).append(observation(run, case))

    flaky = [t for key, obs in per_test.items() if (t := classify(key, obs)) is not None]
    flaky.sort(key=lambda t: (-t.retried, -t.flip_rate, -t.flips, t.name))
    broken_tests = [b for key, obs in per_test.items() if (b := broken(key, obs)) is not None]
    broken_tests.sort(key=lambda b: (-b.failing_runs, b.name))
    return FlakyReport(
        cycle=cycle,
        window=window,
        runs_analyzed=analyzed,
        tests_analyzed=len(per_test),
        flaky=tuple(flaky),
        broken=tuple(broken_tests),
    )


def find_flaky_tests(*, cycle: str, window: int = DEFAULT_WINDOW) -> FlakyReport:
    """Flaky tests across the last ``window`` runs of ``cycle``, most flips first."""
    window = max(2, int(window))
    return _report(cycle, _cycle_runs(cycle, limit=window), window)


def newly_flaky_tests(
    *, cycle: str, run_id: str | None = None, window: int = DEFAULT_WINDOW
) -> tuple[FlakyTest, ...]:
    """Tests that ``run_id`` (default: the cycle's latest run) made flaky.

    Flaky in the ``window`` runs ending at that run, and not in the ``window``
    runs ending at the run before it. Alerts and ``testo flaky --new`` use this
    so a test is announced once, when it starts flipping, not on every run.
    """
    window = max(2, int(window))
    runs = _cycle_runs(cycle)
    start = next((i for i, r in enumerate(runs) if r.run_id == run_id), None) if run_id else 0
    if start is None:
        return ()
    now = _report(cycle, runs[start : start + window], window).flaky
    if not now:
        return ()
    before = {t.key for t in _report(cycle, runs[start + 1 : start + 1 + window], window).flaky}
    return tuple(t for t in now if t.key not in before)
