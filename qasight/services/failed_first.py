"""Order a suite so the tests most likely to fail run first, from the shared run history.

Used by ``pytest --qasight-failed-first``. pytest's own ``--ff`` reads one machine's
cache; this reads the cycle's recent runs (on this machine, or on the team
server), so a fresh CI runner also puts last run's failures first and a red
build turns red in its first minute.

A test's rank, lowest first:

0. it failed (or broke) in the latest run it was in;
1. it is flaky (passed and failed, flipping, over the window);
2. it failed at least once in the window;
3. everything else, including tests with no history.

The sort is stable, so inside a rank tests keep the order they were collected in.
Nothing is skipped: only the order changes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

FAILED_LAST = 0
FLAKY = 1
FAILED_RECENTLY = 2
REST = 3
_FAIL = frozenset({"failed", "broken"})


def rank_of(item: Mapping[str, Any]) -> int:
    if str(item.get("last_status") or "") in _FAIL:
        return FAILED_LAST
    if item.get("flaky"):
        return FLAKY
    if int(item.get("failed") or 0) > 0:
        return FAILED_RECENTLY
    return REST


def rank_index(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Rank by full name and by key; only tests that rank above :data:`REST`."""
    out: dict[str, int] = {}
    for item in items:
        rank = rank_of(item)
        if rank == REST:
            continue
        for name in (str(item.get("full_name") or ""), str(item.get("key") or "")):
            if name and rank < out.get(name, REST):
                out[name] = rank
    return out


def order(
    tests: Sequence[str],
    ranks: Mapping[str, int],
    *,
    names_for: Callable[[str], Iterable[str]] = lambda test: (test,),
) -> tuple[list[int], dict[int, int]]:
    """Positions of ``tests`` in run order, and how many tests got each rank below REST."""
    found = [min((ranks.get(n, REST) for n in names_for(t)), default=REST) for t in tests]
    positions = sorted(range(len(tests)), key=lambda i: found[i])
    counts: dict[int, int] = {}
    for rank in found:
        if rank != REST:
            counts[rank] = counts.get(rank, 0) + 1
    return positions, counts
