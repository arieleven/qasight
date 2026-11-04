"""Split a suite into shards that take about the same time, from the times history stored.

Used by ``qasight split`` and ``pytest --qasight-shard I/N``. A test's weight is its
median duration over the cycle's recent runs (:mod:`qasight.services.test_history`,
or the team server's ``GET /api/v1/analytics/tests``). Tests with no history weigh
the median of the known ones, so with no history at all the split is round robin.

Shards are filled longest test first, each test going to the shard with the least
time so far (the "longest processing time" rule: never worse than 4/3 of the best
split). The split depends only on the set of tests and their times, not their order;
inside a shard, tests keep the order they were given in.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from heapq import heapify, heappop, heappush
from statistics import median
from typing import Any

_HTTP_TIMEOUT_S = 15.0
_SHARD_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class SplitError(ValueError):
    """Bad shard spec, or timings that could not be loaded."""


@dataclass(frozen=True)
class Shard:
    index: int  # 1-based, like ``--index`` and ``I/N``
    tests: tuple[str, ...]
    estimate_ms: float


@dataclass(frozen=True)
class SplitPlan:
    shards: tuple[Shard, ...]
    # How many of the tests had a time from history.
    timed: int
    total: int

    def shard(self, index: int) -> Shard:
        return self.shards[index - 1]


def parse_shard(raw: str) -> tuple[int, int]:
    """``"2/4"`` -> ``(2, 4)``: shard 2 of 4, counted from 1."""
    match = _SHARD_RE.match(raw or "")
    if not match:
        raise SplitError(f"{raw!r} is not a shard like 2/4 (this one / how many).")
    index, count = int(match.group(1)), int(match.group(2))
    if count < 1 or not 1 <= index <= count:
        raise SplitError(f"shard {raw!r}: the first number must be from 1 to the second.")
    return index, count


def nodeid_names(nodeid: str) -> tuple[str, ...]:
    """Names a pytest node id may have in history, most exact first.

    ``tests/a/test_x.py::TestC::test_y[1]`` was recorded as ``tests.a.test_x.TestC.test_y[1]``
    from JUnit XML (pytest's own mangling) and as ``tests.a.test_x.TestC#test_y`` by
    allure-pytest (no parameters).
    """
    path, bracket, params = nodeid.partition("[")
    parts = path.split("::")
    if len(parts) < 2:
        return (nodeid,)
    module = re.sub(r"\.py$", "", parts[0].replace("/", ".").replace("\\", "."))
    classes = parts[1:-1]
    junit = ".".join([module, *classes, parts[-1] + bracket + params])
    allure = ".".join([module, *classes]) + "#" + parts[-1]
    return (junit, allure, nodeid)


def plan_split(
    tests: Sequence[str],
    timings: Mapping[str, float],
    shards: int,
    *,
    names_for: Callable[[str], Iterable[str]] = lambda test: (test,),
) -> SplitPlan:
    """Split ``tests`` into ``shards`` groups of about equal time."""
    if shards < 1:
        raise SplitError("need at least one shard.")
    known: list[float | None] = []
    for test in tests:
        known.append(next((timings[n] for n in names_for(test) if n in timings), None))
    times = [t for t in known if t is not None]
    # A test with no history weighs what a typical test does; with no history at
    # all every test weighs the same, which makes the split round robin.
    fallback = float(median(times)) if times else 1.0
    weights = [t if t is not None else fallback for t in known]

    heap = [(0.0, i) for i in range(shards)]
    heapify(heap)
    members: list[list[int]] = [[] for _ in range(shards)]
    totals = [0.0] * shards
    # Ties go by name, so the split doesn't depend on the order the tests came in.
    for i in sorted(range(len(tests)), key=lambda i: (-weights[i], tests[i], i)):
        total, shard = heappop(heap)
        members[shard].append(i)
        totals[shard] = total + weights[i]
        heappush(heap, (totals[shard], shard))
    return SplitPlan(
        shards=tuple(
            Shard(
                index=s + 1,
                tests=tuple(tests[i] for i in sorted(members[s])),
                estimate_ms=round(totals[s] if times else 0.0, 1),
            )
            for s in range(shards)
        ),
        timed=len(times),
        total=len(tests),
    )


@dataclass(frozen=True)
class TestTime:
    """One test of the cycle's history: its name and median time (None if never timed)."""

    __test__ = False  # not a pytest test class

    name: str
    key: str
    stage: str
    median_ms: float | None


def timings_index(tests: Iterable[TestTime]) -> dict[str, float]:
    """Median ms by full name and by key, for :func:`plan_split`."""
    out: dict[str, float] = {}
    for test in tests:
        if test.median_ms is None:
            continue
        for name in (test.name, test.key):
            if name:
                out.setdefault(name, test.median_ms)
    return out


def _from_items(items: Iterable[Mapping[str, Any]], stage: str | None) -> list[TestTime]:
    out: list[TestTime] = []
    for item in items:
        if stage and item.get("stage") != stage:
            continue
        ms = item.get("median_duration_ms")
        key = str(item.get("key") or "")
        out.append(
            TestTime(
                name=str(item.get("full_name") or key),
                key=key,
                stage=str(item.get("stage") or ""),
                median_ms=float(ms) if ms is not None else None,
            )
        )
    return out


def load_test_items(
    cycle: str,
    *,
    server: str | None = None,
    token: str | None = None,
    window: int = 20,
) -> list[dict[str, Any]]:
    """Each test in ``cycle``'s last ``window`` runs, as the ``/analytics/tests`` items.

    Read on this machine, or from ``server`` (a team server's history).
    """
    if server:
        import requests

        url = f"{server.rstrip('/')}/api/v1/analytics/tests"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            resp = requests.get(
                url,
                params={"cycle": cycle, "window": str(window)},
                headers=headers,
                timeout=_HTTP_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise SplitError(f"could not reach {url}: {exc}") from exc
        if resp.status_code != 200:
            raise SplitError(f"{url} answered {resp.status_code}.")
        try:
            items = resp.json().get("items") or []
        except (ValueError, AttributeError) as exc:
            raise SplitError(f"{url} answered with something that is not test times.") from exc
        return [dict(item) for item in items if isinstance(item, Mapping)]

    from qasight.services.test_history import list_cycle_tests

    return [dict(vars(t)) for t in list_cycle_tests(cycle, window=window).tests]


def load_test_times(
    cycle: str,
    *,
    stage: str | None = None,
    server: str | None = None,
    token: str | None = None,
    window: int = 20,
) -> list[TestTime]:
    """The tests in ``cycle``'s last ``window`` runs (on this machine, or on ``server``)."""
    return _from_items(load_test_items(cycle, server=server, token=token, window=window), stage)
