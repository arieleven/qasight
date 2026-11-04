"""Quarantine kept in the run history (qasight/services/quarantine_store.py):
the store, the merge into a run's rules, and auto-release."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import create_engine

from qasight.config.loader import load_config_or_defaults
from qasight.config.schema import Plan, QuarantineRule
from qasight.repository.db_config import reset_engine_cache
from qasight.services import flaky_service
from qasight.services import quarantine_store as qs
from qasight.services.cycle_run import (
    CycleRunOptions,
    CycleRunService,
    junit_ingest_plan,
    quarantine_for_run,
)
from qasight.services.quarantine_store import QuarantineError, QuarantineStore
from tests.fixtures.engine import NoopRenderer

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.fixture
def store(tmp_path: Path) -> QuarantineStore:
    return QuarantineStore(create_engine(f"sqlite:///{tmp_path / 'history.db'}"))


# --- the store ------------------------------------------------------------------


def test_add_list_rules_and_remove(store: QuarantineStore) -> None:
    first = store.add(
        cycle="api", test=" test_checkout ", reason=" races ", owner="@ada", added_by="ada"
    )
    assert (first.test, first.reason, first.owner, first.added_by) == (
        "test_checkout",
        "races",
        "@ada",
        "ada",
    )
    assert not first.is_glob
    store.add(cycle="api", test="tests.payments#*")
    store.add(cycle="web", test="test_login")

    assert [e.test for e in store.list("api")] == ["tests.payments#*", "test_checkout"]
    assert {e.cycle for e in store.list()} == {"api", "web"}
    # Rules come oldest first, like a yaml list grows.
    assert store.rules("api") == (
        QuarantineRule(test="test_checkout", reason="races", owner="@ada"),
        QuarantineRule(test="tests.payments#*"),
    )
    assert store.list("api")[0].is_glob

    # Adding again replaces, so a cycle lists a test once.
    store.add(cycle="api", test="test_checkout", reason="still races")
    assert [e.reason for e in store.list("api") if e.test == "test_checkout"] == ["still races"]

    assert store.remove(cycle="api", test="test_checkout") is True
    assert store.remove(cycle="api", test="test_checkout") is False
    assert store.rules("api") == (QuarantineRule(test="tests.payments#*"),)


@pytest.mark.parametrize(("cycle", "test"), [("", "t"), ("api", "  "), ("api", "x" * 1025)])
def test_bad_entries_are_refused(store: QuarantineStore, cycle: str, test: str) -> None:
    with pytest.raises(QuarantineError):
        store.add(cycle=cycle, test=test)


# --- merging into a run's rules --------------------------------------------------


def test_merge_keeps_order_and_drops_repeats() -> None:
    a, b, c = QuarantineRule(test="a"), QuarantineRule(test="b", reason="x"), QuarantineRule("c")
    assert qs.merge_rules((a, b), (QuarantineRule(test="b"), c), ()) == (a, b, c)


def test_a_run_gets_config_and_stored_rules(store: QuarantineStore, tmp_path: Path) -> None:
    store.add(cycle="web", test="test_flaky_refund")
    plan = junit_ingest_plan(cycle="web", reports=["junit.xml"], target_repo=tmp_path)
    merged = qs.with_stored_rules(
        plan, config_rules=(QuarantineRule(test="test_cfg"),), store=store
    )
    assert [r.test for r in merged.quarantine] == ["test_cfg", "test_flaky_refund"]
    # Nothing to add: the same plan back.
    empty = Plan(name="other", description=None, stages=plan.stages)
    assert qs.with_stored_rules(empty, store=store) is empty


def test_a_database_error_leaves_the_run_its_config_rules(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Broken:
        def rules(self, cycle: str) -> tuple[QuarantineRule, ...]:
            raise RuntimeError("database is locked")

    with caplog.at_level("WARNING"):
        assert qs.stored_rules("web", store=Broken()) == ()  # type: ignore[arg-type]
    assert "could not read the stored list for web: database is locked" in caplog.text


def test_quarantine_for_run_reads_the_store_only_when_the_run_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'h.db'}")
    reset_engine_cache()
    try:
        QuarantineStore().add(cycle="web", test="test_x")
        plan = junit_ingest_plan(cycle="web", reports=["junit.xml"], target_repo=tmp_path)
        cfg = load_config_or_defaults(config_path=None)
        assert [r.test for r in quarantine_for_run(cfg=cfg, plan=plan).quarantine] == ["test_x"]
        assert quarantine_for_run(cfg=cfg, plan=plan, stored=False) is plan
    finally:
        reset_engine_cache()


_FAILING_JUNIT = (
    '<testsuite name="pay"><testcase classname="pay" name="test_ok"/>'
    '<testcase classname="pay" name="test_flaky_refund"><failure message="boom">trace</failure>'
    "</testcase></testsuite>"
)


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    monkeypatch.setattr("qasight.paths.ORCHESTRATOR_ROOT", tmp_path)
    monkeypatch.setattr("qasight.paths.STATIC_HISTORY_ROOT", tmp_path / "static" / "history")
    monkeypatch.setenv(qs.RELEASE_AFTER_ENV, "0")
    monkeypatch.chdir(tmp_path)
    from qasight.repository.db import reset_repository_cache

    reset_engine_cache()
    reset_repository_cache()
    (tmp_path / "junit.xml").write_text(_FAILING_JUNIT, encoding="utf-8")
    yield tmp_path
    reset_repository_cache()
    reset_engine_cache()


def _ingest(repo: Path) -> int:
    cfg = load_config_or_defaults(config_path=None)
    plan = junit_ingest_plan(cycle="web", reports=["junit.xml"], target_repo=repo)
    outcome = CycleRunService().run(
        cfg=cfg,
        plan=plan,
        renderer=NoopRenderer(),
        options=CycleRunOptions(force=True, report_db=False, artifacts_root=repo / "artifacts"),
    )
    return outcome.exit_code


def test_the_engine_path_mutes_a_stored_test(history: Path) -> None:
    # qasight run, qasight ingest and the server's ingest route all go through CycleRunService.run.
    assert _ingest(history) == 1
    QuarantineStore().add(cycle="web", test="test_flaky_refund")
    assert _ingest(history) == 0
    QuarantineStore().add(cycle="web", test="test_other")
    QuarantineStore().remove(cycle="web", test="test_flaky_refund")
    assert _ingest(history) == 1


# --- auto-release -------------------------------------------------------------------


def _fake_history(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, dict[str, Any]]]) -> None:
    """``runs``: newest first, each ``{key: case}``."""
    views = [SimpleNamespace(run_id=f"r{i}", cases=cases) for i, cases in enumerate(runs)]
    monkeypatch.setattr(flaky_service, "_cycle_runs", lambda cycle: views)
    monkeypatch.setattr(flaky_service, "_cases_for_run", lambda run: run.cases)


def _case(name: str, status: str = "passed", **extra: Any) -> dict[str, dict[str, Any]]:
    return {name: {"name": name, "full_name": f"tests.api#{name}", "status": status, **extra}}


def test_release_after_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(qs.RELEASE_AFTER_ENV, raising=False)
    assert qs.release_after() == 10
    for raw, want in (("3", 3), ("0", 0), ("-2", 0), ("soon", 10)):
        monkeypatch.setenv(qs.RELEASE_AFTER_ENV, raw)
        assert qs.release_after() == want


def test_exact_names_are_released_after_n_clean_runs(
    store: QuarantineStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.add(cycle="api", test="test_fixed")
    store.add(cycle="api", test="tests.api#test_full")  # matched by full name
    store.add(cycle="api", test="test_still_flaky")
    store.add(cycle="api", test="test_retried")
    store.add(cycle="api", test="test_new")
    store.add(cycle="api", test="test_*")  # globs stay
    newest_first = [
        {
            **_case("test_fixed"),
            **_case("test_full"),
            **_case("test_still_flaky"),
            **_case("test_retried", retried=True),
            **_case("test_new"),
        },
        {**_case("test_fixed"), **_case("test_full"), **_case("test_still_flaky", "failed")},
        # Skipped (or absent) runs don't count either way.
        {**_case("test_fixed", "skipped"), **_case("test_retried")},
        {**_case("test_fixed"), **_case("test_full"), **_case("test_retried")},
    ]
    _fake_history(monkeypatch, newest_first)

    released = qs.auto_release("api", store=store, after=3)
    assert sorted(e.test for e in released) == ["test_fixed", "tests.api#test_full"]
    assert sorted(e.test for e in store.list("api")) == [
        "test_*",
        "test_new",
        "test_retried",
        "test_still_flaky",
    ]
    assert qs.auto_release("api", store=store, after=0) == []


def test_release_records_an_audit_event_in_accounts_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.accounts import AccountStore

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'h.db'}")
    monkeypatch.setenv("TESTO_AUTH", "accounts")
    reset_engine_cache()
    try:
        QuarantineStore().add(cycle="api", test="test_fixed")
        _fake_history(monkeypatch, [_case("test_fixed")] * 2)
        assert [e.test for e in qs.auto_release("api", after=2)] == ["test_fixed"]
        (entry,) = AccountStore().recent(5)
        assert (entry.username, entry.method) == ("qasight", "RELEASE")
        assert entry.path == "/api/v1/quarantine?cycle=api&test=test_fixed"
    finally:
        reset_engine_cache()


def test_the_release_hook_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(qs.RELEASE_AFTER_ENV, "2")

    def boom(cycle: str, **_: Any) -> list[Any]:
        raise RuntimeError("no database")

    monkeypatch.setattr(qs, "auto_release", boom)
    with caplog.at_level("WARNING"):
        qs.release_after_run(cycle="api", run_id="r1")
    assert "auto-release after run r1 of api failed: no database" in caplog.text


# --- auto-quarantine (TESTO_QUARANTINE_AUTO) -------------------------------------


def _flaky(name: str, *, rate: float, last: str = "failed") -> flaky_service.FlakyTest:
    return flaky_service.FlakyTest(
        key=name,
        name=name,
        group="g",
        runs=10,
        passed=6,
        failed=4,
        flips=5,
        flip_rate=rate,
        last_status=last,
        history=(),
        full_name=f"tests.api#{name}",
        reasons=("flipped 5 times across runs",),
    )


def _report(monkeypatch: pytest.MonkeyPatch, *tests: flaky_service.FlakyTest) -> None:
    report = flaky_service.FlakyReport(
        cycle="api", window=20, runs_analyzed=10, tests_analyzed=10, flaky=tests
    )
    monkeypatch.setattr(flaky_service, "find_flaky_tests", lambda **_: report)


def test_auto_threshold_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(qs.AUTO_ENV, raising=False)
    assert qs.auto_threshold() == 0.0
    for raw, want in (("0.3", 0.3), ("30", 0.3), ("30%", 0.3), ("-1", 0.0), ("lots", 0.0)):
        monkeypatch.setenv(qs.AUTO_ENV, raw)
        assert qs.auto_threshold() == pytest.approx(want)


def test_failing_flaky_tests_over_the_rate_are_quarantined(
    store: QuarantineStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.add(cycle="api", test="test_known*")
    _report(
        monkeypatch,
        _flaky("test_race", rate=0.5),
        _flaky("test_param[1-a]", rate=0.4),
        _flaky("test_rare", rate=0.1),
        _flaky("test_passed_now", rate=0.6, last="passed"),
        _flaky("test_known_one", rate=0.9),
    )

    added = qs.auto_quarantine("api", store=store, threshold=0.3)

    assert [e.test for e in added] == ["test_race", "test_param[[]1-a]"]
    assert added[0].added_by == "qasight" and "flip rate 50%" in added[0].reason
    # The escaped name matches only that test, and can be released like any exact name.
    assert qs.literal_name("test_param[[]1-a]") == "test_param[1-a]"
    assert qs.literal_name("test_*") is None
    assert qs.auto_quarantine("api", store=store, threshold=0.3) == []
    assert qs.auto_quarantined_since("api", 0, store=store) == ["test_param[1-a]", "test_race"]
    assert qs.auto_quarantine("api", store=store, threshold=0) == []


def test_an_escaped_entry_is_released_after_clean_runs(
    store: QuarantineStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.add(cycle="api", test=qs.escape_name("test_p[x]"))
    _fake_history(monkeypatch, [_case("test_p[x]")] * 2)

    assert [e.test for e in qs.auto_release("api", store=store, after=2)] == ["test_p[[]x]"]


def test_the_quarantine_hook_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(qs.RELEASE_AFTER_ENV, "0")
    monkeypatch.setenv(qs.AUTO_ENV, "0.3")

    def boom(cycle: str, **_: Any) -> list[Any]:
        raise RuntimeError("no database")

    monkeypatch.setattr(qs, "auto_quarantine", boom)
    with caplog.at_level("WARNING"):
        qs.quarantine_after_run(cycle="api", run_id="r1")
    assert "auto-quarantine after run r1 of api failed: no database" in caplog.text
