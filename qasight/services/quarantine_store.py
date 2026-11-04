"""Quarantine kept in the run-history database, set from the UI, the API or ``qasight quarantine``.

The ``quarantine:`` list in ``qasight.yaml`` needs a config file and a
code review, which ``qasight ingest`` and ``pytest --qasight`` users often don't
have. This list lives next to the runs (table ``quarantined_test``), one entry
per cycle and test, and is added to the cycle's config rules wherever a run is
judged: :meth:`~qasight.services.cycle_run.CycleRunService.run` (``qasight run``,
``qasight ingest``, the server's ingest route, API runs) and the pytest plugin.
The gate itself is the same one as for config rules
(:mod:`qasight.engine.quarantine`): mute, not skip.

An entry whose test is an exact name (no glob characters) is released on its
own once the test passed each of its last :func:`release_after` runs of the
cycle (``TESTO_QUARANTINE_RELEASE_AFTER``, default 10, ``0`` turns it off).
A retried pass doesn't count. Glob entries stay until someone removes them.

With ``TESTO_QUARANTINE_AUTO`` set to a flip rate (``0.3``), a test that is
flaky at that rate or more and failed in the run just recorded is added on its
own (``added_by`` ``qasight``), so the next run doesn't fail on it; the pull
request comment lists it. Names with ``*``, ``?`` or ``[`` (pytest's
``test_x[1]``) are stored escaped (``test_x[[]1]``) and still released.

Everything that reads the list from a run is best-effort: a database error is
logged once and the run goes on with the config rules alone.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import urlencode

from qasight.config.schema import Plan, QuarantineRule

logger = logging.getLogger(__name__)

RELEASE_AFTER_ENV: Final[str] = "TESTO_QUARANTINE_RELEASE_AFTER"
DEFAULT_RELEASE_AFTER: Final[int] = 10
MAX_TEST = 1024
MAX_REASON = 500
MAX_OWNER = 200
# Who released an entry on its own, in the audit log and the CLI output.
AUTO_RELEASE_ACTOR: Final[str] = "qasight"
_GLOB_CHARS = frozenset("*?[")


class QuarantineError(ValueError):
    """An entry with no cycle or no test."""


@dataclass(frozen=True)
class QuarantineEntry:
    cycle: str
    test: str
    reason: str
    owner: str
    added_by: str | None
    added_at: datetime

    @property
    def is_glob(self) -> bool:
        return is_glob(self.test)

    def rule(self) -> QuarantineRule:
        return QuarantineRule(test=self.test, reason=self.reason or None, owner=self.owner or None)


def is_glob(test: str) -> bool:
    """``test`` has ``fnmatch`` wildcards, so it isn't one test's name."""
    return any(ch in _GLOB_CHARS for ch in test)


_ESCAPED_CHAR = re.compile(r"\[([*?\[])\]")


def escape_name(name: str) -> str:
    """A pattern that matches exactly *name*: ``*``, ``?`` and ``[`` in brackets."""
    return re.sub(r"([*?\[])", r"[\1]", name)


def literal_name(test: str) -> str | None:
    """The one test name *test* matches: itself, an escaped name, or ``None`` for a real glob."""
    if not is_glob(test):
        return test
    name = _ESCAPED_CHAR.sub(lambda m: m.group(1), test)
    return name if escape_name(name) == test else None


def _id(cycle: str, test: str) -> str:
    return hashlib.sha256(f"{cycle}\n{test}".encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _entry(row: Any) -> QuarantineEntry:
    return QuarantineEntry(
        cycle=row.cycle,
        test=row.test,
        reason=row.reason,
        owner=row.owner,
        added_by=row.added_by,
        added_at=_aware(row.added_at),
    )


class QuarantineStore:
    """Quarantined tests on a SQLAlchemy engine (the run-history DB by default)."""

    def __init__(self, engine: Any = None) -> None:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        self._engine = engine if engine is not None else get_engine()
        create_db_and_tables(self._engine)

    def add(
        self,
        *,
        cycle: str,
        test: str,
        reason: str | None = None,
        owner: str | None = None,
        added_by: str | None = None,
    ) -> QuarantineEntry:
        """Quarantine ``test`` (a name or a glob) in ``cycle``; replaces an earlier entry for it."""
        from sqlmodel import Session

        from qasight.repository.models import QuarantinedTest

        cycle, test = cycle.strip(), test.strip()
        if not cycle or not test:
            raise QuarantineError("a quarantine entry needs a cycle and a test name or glob")
        if len(cycle) > 512 or len(test) > MAX_TEST:
            raise QuarantineError(f"cycle or test name is too long (test: {MAX_TEST} characters)")
        row = QuarantinedTest(
            id=_id(cycle, test),
            cycle=cycle,
            test=test,
            reason=(reason or "").strip()[:MAX_REASON],
            owner=(owner or "").strip()[:MAX_OWNER],
            added_by=added_by[:64] if added_by else None,
            added_at=datetime.now(tz=UTC),
        )
        with Session(self._engine) as session:
            session.merge(row)
            session.commit()
        return _entry(row)

    def remove(self, *, cycle: str, test: str) -> bool:
        """Release ``test`` in ``cycle``; False if it wasn't in the list."""
        from sqlmodel import Session

        from qasight.repository.models import QuarantinedTest

        with Session(self._engine) as session:
            row = session.get(QuarantinedTest, _id(cycle.strip(), test.strip()))
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def list(self, cycle: str | None = None) -> list[QuarantineEntry]:
        """Every entry, newest first, optionally only ``cycle``'s."""
        from sqlmodel import Session, col, select

        from qasight.repository.models import QuarantinedTest

        query = select(QuarantinedTest).order_by(col(QuarantinedTest.added_at).desc())
        if cycle:
            query = query.where(QuarantinedTest.cycle == cycle)
        with Session(self._engine) as session:
            return [_entry(row) for row in session.exec(query).all()]

    def rules(self, cycle: str) -> tuple[QuarantineRule, ...]:
        """``cycle``'s entries as rules for :mod:`qasight.engine.quarantine`, oldest first."""
        return tuple(entry.rule() for entry in reversed(self.list(cycle)))


# --- used by the engine path ------------------------------------------------


def merge_rules(*groups: tuple[QuarantineRule, ...]) -> tuple[QuarantineRule, ...]:
    """The rules of every group in order, each test pattern once (the first one wins)."""
    seen: set[str] = set()
    out: list[QuarantineRule] = []
    for group in groups:
        for rule in group:
            if rule.test not in seen:
                seen.add(rule.test)
                out.append(rule)
    return tuple(out)


def stored_rules(cycle: str, *, store: QuarantineStore | None = None) -> tuple[QuarantineRule, ...]:
    """``cycle``'s stored rules, or none (with one log line) when the database can't be read."""
    try:
        return (store or QuarantineStore()).rules(cycle)
    except Exception as exc:  # noqa: BLE001 - a run must not fail because the list can't be read
        logger.warning("quarantine: could not read the stored list for %s: %s", cycle, exc)
        return ()


def with_stored_rules(
    plan: Plan,
    *,
    config_rules: tuple[QuarantineRule, ...] = (),
    store: QuarantineStore | None = None,
) -> Plan:
    """``plan`` with ``config_rules`` and the stored entries for its cycle added to its own rules."""
    import dataclasses

    merged = merge_rules(plan.quarantine, config_rules, stored_rules(plan.name, store=store))
    if merged == plan.quarantine:
        return plan
    return dataclasses.replace(plan, quarantine=merged)


# --- auto-release -------------------------------------------------------------


def release_after() -> int:
    """Clean passing runs in a row that release an entry (``TESTO_QUARANTINE_RELEASE_AFTER``); 0 is off."""
    raw = os.getenv(RELEASE_AFTER_ENV, "").strip()
    if not raw:
        return DEFAULT_RELEASE_AFTER
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(
            "quarantine: %s=%r is not a number; using %d",
            RELEASE_AFTER_ENV,
            raw,
            DEFAULT_RELEASE_AFTER,
        )
        return DEFAULT_RELEASE_AFTER


def _recent_outcomes(cycle: str, test: str, wanted: int) -> list[str]:
    """The test's outcomes in its latest runs of ``cycle``, newest first, at most ``wanted``.

    ``"pass"`` is a pass on the first try; a pass after a retry is ``"retried"``.
    Runs where the test was skipped or absent are not counted.
    """
    from qasight.services import flaky_service

    out: list[str] = []
    # A test missing from most runs (sharded, renamed) shouldn't scan the whole history.
    for run in flaky_service._cycle_runs(cycle)[: wanted * 3]:
        for case in flaky_service._cases_for_run(run).values():
            if test not in (case.get("name"), case.get("full_name")):
                continue
            status = str(case.get("status") or "")
            if status == "passed":
                out.append("retried" if case.get("retried") else "pass")
            elif status in ("failed", "broken"):
                out.append("fail")
            else:
                continue
            break
        if len(out) >= wanted:
            break
    return out


def releasable(cycle: str, *, store: QuarantineStore, after: int) -> list[QuarantineEntry]:
    """Exact-name entries of ``cycle`` whose test passed (first try) each of its last ``after`` runs."""
    if after <= 0:
        return []
    out: list[QuarantineEntry] = []
    for entry in store.list(cycle):
        name = literal_name(entry.test)
        if name is None:
            continue
        outcomes = _recent_outcomes(cycle, name, after)
        if len(outcomes) >= after and all(o == "pass" for o in outcomes):
            out.append(entry)
    return out


def auto_release(
    cycle: str, *, store: QuarantineStore | None = None, after: int | None = None
) -> list[QuarantineEntry]:
    """Remove the entries :func:`releasable` finds; returns them."""
    after = release_after() if after is None else after
    if after <= 0:
        return []
    store = store or QuarantineStore()
    released: list[QuarantineEntry] = []
    for entry in releasable(cycle, store=store, after=after):
        if store.remove(cycle=entry.cycle, test=entry.test):
            released.append(entry)
            record_audit(
                username=AUTO_RELEASE_ACTOR, method="RELEASE", cycle=cycle, test=entry.test
            )
            logger.info(
                "quarantine: released %s in %s after %d clean runs", entry.test, cycle, after
            )
    return released


def release_after_run(*, cycle: str, run_id: str) -> None:
    """Hook after a recorded run: :func:`auto_release` for its cycle. Never raises."""
    if release_after() <= 0:
        return
    try:
        auto_release(cycle)
    except Exception as exc:  # noqa: BLE001 - never let the release pass change a run's outcome
        logger.warning("quarantine: auto-release after run %s of %s failed: %s", run_id, cycle, exc)


# --- auto-quarantine ------------------------------------------------------------

AUTO_ENV: Final[str] = "TESTO_QUARANTINE_AUTO"


def auto_threshold() -> float:
    """The flip rate (0-1) at which a failing flaky test is quarantined; 0 is off (the default)."""
    raw = os.getenv(AUTO_ENV, "").strip().rstrip("%")
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning("quarantine: %s=%r is not a flip rate like 0.3; off", AUTO_ENV, raw)
        return 0.0
    # "30" means 30%.
    return min(1.0, value / 100 if value > 1 else max(0.0, value))


def auto_quarantine(
    cycle: str, *, store: QuarantineStore | None = None, threshold: float | None = None
) -> list[QuarantineEntry]:
    """Quarantine the tests of *cycle* that are flaky at *threshold* or more and failed last run.

    Only tests that failed in the latest run: one that was released after clean
    runs comes back only when it fails again. Tests a rule already covers, and
    tests that fail every time (broken, not flaky), are left alone.
    """
    from qasight.engine.quarantine import matching_rule
    from qasight.services.flaky_service import find_flaky_tests

    threshold = auto_threshold() if threshold is None else threshold
    if threshold <= 0:
        return []
    store = store or QuarantineStore()
    rules = store.rules(cycle)
    added: list[QuarantineEntry] = []
    for test in find_flaky_tests(cycle=cycle).flaky:
        if test.flip_rate < threshold or test.last_status not in ("failed", "broken"):
            continue
        if matching_rule(rules, name=test.name, full_name=test.full_name):
            continue
        entry = store.add(
            cycle=cycle,
            test=escape_name(test.name),
            reason=f"automatic: flip rate {test.flip_rate:.0%} over {test.runs} runs "
            f"({'; '.join(test.reasons)})",
            added_by=AUTO_RELEASE_ACTOR,
        )
        rules = (*rules, entry.rule())
        added.append(entry)
        record_audit(username=AUTO_RELEASE_ACTOR, method="POST", cycle=cycle, test=entry.test)
        logger.info("quarantine: added %s in %s (flip rate %.2f)", test.name, cycle, test.flip_rate)
    return added


def auto_quarantined_since(
    cycle: str, since: float, *, store: QuarantineStore | None = None
) -> list[str]:
    """Tests of *cycle* quarantined automatically at or after *since* (epoch seconds).

    For the pull request comment. Never raises; an unreadable list is no list.
    """
    try:
        entries = (store or QuarantineStore()).list(cycle)
    except Exception:  # noqa: BLE001
        return []
    return sorted(
        literal_name(e.test) or e.test
        for e in entries
        if e.added_by == AUTO_RELEASE_ACTOR and e.added_at.timestamp() >= since
    )


def quarantine_after_run(*, cycle: str, run_id: str) -> None:
    """Hook after a recorded run: release, then :func:`auto_quarantine`. Never raises."""
    release_after_run(cycle=cycle, run_id=run_id)
    if auto_threshold() <= 0:
        return
    try:
        auto_quarantine(cycle)
    except Exception as exc:  # noqa: BLE001 - never let the quarantine pass change a run's outcome
        logger.warning(
            "quarantine: auto-quarantine after run %s of %s failed: %s", run_id, cycle, exc
        )


# --- audit ------------------------------------------------------------------------


def audit_path(cycle: str, test: str) -> str:
    """The path an audit line names for a quarantine change (the API route, with its query)."""
    return "/api/v1/quarantine?" + urlencode({"cycle": cycle, "test": test})


def record_audit(*, username: str, method: str, cycle: str, test: str) -> None:
    """Record a quarantine change made outside the API in the audit log (``TESTO_AUTH=accounts``).

    API writes are audited by the API itself; this covers ``qasight quarantine``
    and auto-release. Best-effort: a lost audit line is only logged.
    """
    if os.getenv("TESTO_AUTH", "").strip().lower() != "accounts":
        return
    try:
        from qasight.accounts import AccountStore

        AccountStore().record(
            username=username[:64], method=method[:8], path=audit_path(cycle, test), status=200
        )
    except Exception:  # noqa: BLE001
        logger.warning("quarantine: could not write the audit log", exc_info=True)
