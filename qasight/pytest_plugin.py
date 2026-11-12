"""pytest plugin: ``pytest --qasight`` records the session as a run, with no CI step or config.

Installed with qasight (entry point ``pytest11``), and inert until asked:
``--qasight`` (or ``--qasight-cycle NAME``, or ``TESTO_RECORD=1``) turns it on.
The session's results are written as JUnit XML (pytest's own ``--junitxml``,
or a temporary file when that isn't set) and recorded exactly like
``qasight ingest`` would: same run record, same history, same reporters. With
``--qasight-server`` (or ``TESTO_SERVER_URL``) they go to a team server instead.

Recording never changes pytest's outcome, with one exception: when every
failed test is quarantined (``quarantine:`` in ``qasight.yaml``, or the
list kept in the run history or on the team server, see
:mod:`qasight.services.quarantine_store`), the session exits 0 instead of 1
and the summary names the muted tests. A problem is a warning line in the
summary. Under pytest-xdist only the controller records. This module is
imported by every pytest run on a machine with qasight installed, so it
imports nothing of qasight until a run is actually recorded.

It also hands out keys from a resource pool (``qasight/resources/``): with
``--qasight-resource POOL``, or inside a stage with ``resources:`` (which sets
``TESTO_RESOURCE_SPEC``), each test process (each pytest-xdist worker, never
the controller) holds its own key for the session, or each test gets one
(``per: test``). The key is in ``TESTO_RESOURCE_ID`` and the ``testo_resource``
fixture.

And it splits a suite into shards of about equal time: ``--qasight-shard I/N``
(or ``TESTO_SHARD=I/N``) runs only shard I of N, from the test times in the
cycle's history (``qasight/services/test_split.py``). Under pytest-xdist the
controller loads the times once and hands them to its workers, so all of them
pick the same tests.

And it runs only the tests a change can affect: ``--qasight-affected`` (or
``TESTO_AFFECTED=1``) diffs against the baseline branch and deselects the rest
(``qasight/services/affected_tests.py``). Anything it can't map to tests
runs the whole suite, and the summary says why.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from qasight.config.schema import QuarantineRule

_RECORD_ENV = "TESTO_RECORD"
# Exit codes after which there is nothing worth recording: interrupted,
# internal error, usage error, no tests collected.
_NOTHING_RAN = frozenset({2, 3, 4, 5})
_TEMP_DIR = pytest.StashKey[Path]()
_SUMMARY = pytest.StashKey[str]()
_QUARANTINE_SUMMARY = pytest.StashKey[str]()
_RESOURCE_REQUEST = pytest.StashKey["dict[str, Any] | None"]()
_SPEC_ENV = "TESTO_RESOURCE_SPEC"
_SHARD_ENV = "TESTO_SHARD"
_SHARD = pytest.StashKey["tuple[int, int] | None"]()
_SHARD_TIMINGS = pytest.StashKey["dict[str, float]"]()
_SHARD_SUMMARY = pytest.StashKey[str]()
_WORKER_TIMINGS = "testo_shard_timings"
_AFFECTED_ENV = "TESTO_AFFECTED"
# (file, node id under the file or None for the whole file) pairs to keep; None runs all.
_AFFECTED = pytest.StashKey["frozenset[tuple[Path, str | None]] | None"]()
_AFFECTED_NOTHING = pytest.StashKey[bool]()
_AFFECTED_SUMMARY = pytest.StashKey["list[str]"]()
_AFFECTED_SHOWN = 20
_FAILED_FIRST_ENV = "TESTO_FAILED_FIRST"
_FAILED_FIRST = pytest.StashKey["dict[str, int] | None"]()
_FAILED_FIRST_SUMMARY = pytest.StashKey[str]()
_WORKER_RANKS = "testo_failed_first_ranks"
_LIVE_ENV = "TESTO_LIVE"
_LIVE = pytest.StashKey["Any"]()
_LIVE_SUMMARY = pytest.StashKey[str]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("qasight", "QASight: record this session as a run")
    group.addoption(
        "--qasight",
        "--testo",
        action="store_true",
        default=False,
        help="Record this session in QASight's run history (see it with `qasight ui`).",
    )
    group.addoption(
        "--qasight-cycle",
        "--testo-cycle",
        default=None,
        metavar="NAME",
        help="Cycle to record under (default: the rootdir's name). Implies --qasight.",
    )
    group.addoption(
        "--qasight-tier",
        "--testo-tier",
        default="unit",
        choices=("unit", "integration", "e2e"),
        help="Test-pyramid tier of these tests (default: unit).",
    )
    group.addoption(
        "--qasight-server",
        "--testo-server",
        default=None,
        metavar="URL",
        help="Upload to this QASight server instead of recording locally "
        "(default: $TESTO_SERVER_URL). The token comes from $TESTO_API_TOKEN.",
    )
    group.addoption(
        "--qasight-no-live",
        action="store_true",
        default=False,
        help="With --qasight-server: don't send each result to the server while tests run "
        "(the report is still uploaded at the end). Also $TESTO_LIVE=0.",
    )
    group.addoption(
        "--qasight-resource",
        "--testo-resource",
        default=None,
        metavar="POOL",
        help="Give each test process its own key from this pool ('resources:' in "
        "qasight.yaml), in $TESTO_RESOURCE_ID and the testo_resource fixture.",
    )
    group.addoption(
        "--qasight-resource-label",
        "--testo-resource-label",
        action="append",
        default=[],
        metavar="LABEL",
        help="Only keys with this label (repeat for several).",
    )
    group.addoption(
        "--qasight-resource-per",
        "--testo-resource-per",
        default="worker",
        choices=("worker", "test"),
        help="Hold one key per test process (default), or take one for each test.",
    )
    group.addoption(
        "--qasight-resource-wait",
        "--testo-resource-wait",
        default="30m",
        metavar="DURATION",
        help="How long to wait for a free key: 90s, 10m, 2h or forever (default: 30m).",
    )
    group.addoption(
        "--qasight-shard",
        "--testo-shard",
        default=None,
        metavar="I/N",
        help="Run only shard I of N, split so shards take about the same time "
        "(default: $TESTO_SHARD).",
    )
    group.addoption(
        "--qasight-affected",
        "--testo-affected",
        action="store_true",
        default=False,
        help="Run only the tests the changes since the baseline branch can affect "
        "('affected:' in qasight.yaml; also $TESTO_AFFECTED=1). Unmapped changes run all.",
    )
    group.addoption(
        "--qasight-affected-base",
        "--testo-affected-base",
        default=None,
        metavar="REF",
        help="Branch or commit --qasight-affected diffs against (default: affected.base, else "
        "the pull request's target branch in CI, else origin/main).",
    )
    group.addoption(
        "--qasight-failed-first",
        "--testo-failed-first",
        action="store_true",
        default=False,
        help="Run the tests that failed or flaked in the cycle's recent runs first, from the "
        "recorded history (or --qasight-server's); nothing is skipped. Also $TESTO_FAILED_FIRST=1.",
    )
    group.addoption(
        "--qasight-timings",
        "--testo-timings",
        default=None,
        metavar="CYCLE",
        help="Cycle whose history --qasight-shard and --qasight-failed-first use (default: "
        "$TESTO_CYCLE, set by `qasight run`, else --qasight-cycle, else the rootdir's name).",
    )


def _enabled(config: pytest.Config) -> bool:
    if hasattr(config, "workerinput"):  # a pytest-xdist worker; the controller records
        return False
    return bool(
        config.getoption("qasight")
        or config.getoption("qasight_cycle")
        or os.environ.get(_RECORD_ENV, "").strip().lower() in ("1", "true", "yes")
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    config.stash[_RESOURCE_REQUEST] = _resource_request(config)
    _configure_shard(config)
    _configure_affected(config)
    _configure_failed_first(config)
    _configure_live(config)
    # Before pytest's junitxml plugin reads the option, so it writes the report for us.
    if not _enabled(config) or config.option.xmlpath:
        return
    # Inside the rootdir when it can be, so the run records the checkout's git
    # branch and commit; removed again when the session ends.
    try:
        folder = Path(tempfile.mkdtemp(prefix=".testo-", dir=config.rootpath))
    except OSError:
        folder = Path(tempfile.mkdtemp(prefix="testo-pytest-"))
    config.option.xmlpath = str(folder / "session.xml")
    config.stash[_TEMP_DIR] = folder


def _cycle(config: pytest.Config) -> str:
    return (config.getoption("qasight_cycle") or config.rootpath.name or "pytest").strip()


def _repo(config: pytest.Config, xml: Path) -> Path:
    """The rootdir when the report is inside it (its git branch and commit are recorded), else the report's folder."""
    root = config.rootpath.resolve()
    return root if root in xml.parents else xml.parent


def _record(config: pytest.Config, xml: Path) -> str:
    """Record *xml*; the line to show in the terminal summary."""
    from rich.console import Console

    from qasight.cli.runner import execute_ingest_command
    from qasight.cli.ui.console import _THEME

    cycle = _cycle(config)
    server = config.getoption("qasight_server") or os.environ.get("TESTO_SERVER_URL") or None
    # The run's panels would repeat pytest's own summary; keep them for the error case.
    quiet = Console(file=io.StringIO(), width=120, theme=_THEME, no_color=True)
    exit_code = execute_ingest_command(
        console=quiet,
        reports=[str(xml)],
        cycle=cycle,
        stage="pytest",
        tier=config.getoption("qasight_tier"),
        repo=_repo(config, xml),
        config_path=None,
        ci=False,
        persist=True,
        server=server,
        token=os.environ.get("TESTO_API_TOKEN") or None,
    )
    # 0 and 1 (tests failed) are both a recorded run; anything else didn't record.
    if exit_code not in (0, 1):
        last = quiet.file.getvalue().strip().splitlines()  # type: ignore[attr-defined]
        detail = f": {last[-1]}" if last else ""
        return f"qasight: could not record this session (exit {exit_code}){detail}"
    where = f"on {server}" if server else "here; see it with `qasight ui`"
    return f"qasight: recorded this session as a run of cycle '{cycle}' ({where})"


def _quarantine_rules(
    config: pytest.Config, cycle: str, server: str | None
) -> tuple[QuarantineRule, ...]:
    """The cycle's quarantine: ``qasight.yaml``'s rules, then the stored list.

    The stored list is the team server's (``GET /api/v1/quarantine``) when
    recording there, else this machine's run history. Either may be missing.
    """
    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.services.quarantine_store import merge_rules, stored_rules

    configured: tuple[QuarantineRule, ...] = ()
    try:
        plan = discover_and_load(cwd=config.rootpath).cycles.get(cycle)
        configured = plan.quarantine if plan is not None else ()
    except ConfigError:
        pass
    if server:
        from qasight.services.ingest_client import fetch_quarantine_rules

        stored = fetch_quarantine_rules(
            server=server, token=os.environ.get("TESTO_API_TOKEN") or None, cycle=cycle
        )
    else:
        stored = stored_rules(cycle)
    return merge_rules(configured, stored)


def _waive_quarantined(session: pytest.Session, xml: Path) -> None:
    """Exit 0 instead of 1 when every failed test is quarantined (mute, not skip).

    The same gate as a ``qasight run`` stage (:mod:`qasight.engine.quarantine`),
    on the session's JUnit XML read the way ``qasight ingest`` reads it.
    """
    from qasight.engine.quarantine import evaluate
    from qasight.reporting.junit_import import import_junit_reports

    config = session.config
    cycle = _cycle(config)
    server = config.getoption("qasight_server") or os.environ.get("TESTO_SERVER_URL") or None
    try:
        rules = _quarantine_rules(config, cycle, server)
        if not rules:
            return
        with tempfile.TemporaryDirectory(prefix="testo-quarantine-") as tmp:
            import_junit_reports(
                target_repo=xml.parent, patterns=(xml.name,), results_dir=Path(tmp), tool="pytest"
            )
            verdict = evaluate(rules, Path(tmp))
    except Exception as exc:  # noqa: BLE001 - the quarantine check must never break the run
        config.stash[_QUARANTINE_SUMMARY] = f"qasight: could not check the quarantine: {exc}"
        return
    if not verdict.waives_stage:
        return
    session.exitstatus = 0
    names = ", ".join(verdict.quarantined)
    config.stash[_QUARANTINE_SUMMARY] = (
        f"qasight: exit 0 because every failed test is quarantined in cycle '{cycle}': {names}"
    )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # trylast: after pytest's junitxml plugin has written the report.
    config = session.config
    if int(exitstatus) == 5 and config.stash.get(_AFFECTED_NOTHING, False):
        # No test needed to run: a pass, not pytest's "no tests collected".
        session.exitstatus = 0
    if not _enabled(config):
        return
    temp = config.stash.get(_TEMP_DIR, None)
    xml = Path(config.option.xmlpath)
    try:
        _finish_live(config)
        if int(exitstatus) in _NOTHING_RAN or not xml.is_file():
            return
        if int(exitstatus) == 1:
            _waive_quarantined(session, xml.resolve())
        config.stash[_SUMMARY] = _record(config, xml.resolve())
    except Exception as exc:  # noqa: BLE001 — recording must never break the test run
        config.stash[_SUMMARY] = f"qasight: could not record this session: {exc}"
    finally:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    for affected_line in config.stash.get(_AFFECTED_SUMMARY, []):
        terminalreporter.write_line(affected_line)
    for key in (
        _SHARD_SUMMARY,
        _FAILED_FIRST_SUMMARY,
        _QUARANTINE_SUMMARY,
        _LIVE_SUMMARY,
        _SUMMARY,
    ):
        line = config.stash.get(key, None)
        if line:
            terminalreporter.write_line(line)


# -- live results -----------------------------------------------------------------


def _live_wanted(config: pytest.Config) -> str | None:
    """The team server to send live results to, or None."""
    if not _enabled(config) or config.getoption("qasight_no_live"):
        return None
    if os.environ.get(_LIVE_ENV, "").strip().lower() in ("0", "false", "no"):
        return None
    return config.getoption("qasight_server") or os.environ.get("TESTO_SERVER_URL") or None


class _LiveResults:
    """Sends each finished test to the team server (registered only when live is on)."""

    def __init__(self, config: pytest.Config, server: str) -> None:
        from qasight.services.live_client import LiveReporter

        self.config = config
        self.total: int | None = None
        self.reporter = LiveReporter(server=server, token=os.environ.get("TESTO_API_TOKEN") or None)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        # Under pytest-xdist the controller collects nothing; the total stays unknown there.
        if session.items:
            self.total = len(session.items)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        outcome = _live_outcome(report)
        if outcome is None or self.reporter.disabled:
            return
        if not self.reporter.started:
            from qasight.services.ingest_client import client_run_metadata

            metadata = client_run_metadata()
            self.reporter.start(
                _cycle(self.config),
                total=self.total,
                branch=metadata.get("ci_ref_name"),
                commit=metadata.get("ci_commit_sha"),
            )
        message = None
        if outcome in ("failed", "error"):
            text = str(getattr(report, "longreprtext", "") or "").strip()
            message = "\n".join(text.splitlines()[-8:]) or None
        self.reporter.add(report.nodeid, outcome, message)


def _live_outcome(report: pytest.TestReport) -> str | None:
    if report.when == "call":
        if getattr(report, "wasxfail", None) is not None:
            return "skipped" if report.skipped else "passed"
        return str(report.outcome)
    if report.when == "setup" and not report.passed:
        return "skipped" if report.skipped else "error"
    if report.when == "teardown" and report.failed:
        return "error"
    return None


def _configure_live(config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):  # the xdist controller reports for its workers
        return
    server = _live_wanted(config)
    if server is None:
        return
    hooks = _LiveResults(config, server)
    config.stash[_LIVE] = hooks.reporter
    config.pluginmanager.register(hooks, "qasight-live")


def _finish_live(config: pytest.Config) -> None:
    reporter = config.stash.get(_LIVE, None)
    if reporter is None:
        return
    reporter.finish()
    if reporter.error:
        config.stash[_LIVE_SUMMARY] = f"qasight: live results stopped: {reporter.error}"


# -- resource pools ---------------------------------------------------------------


def _resource_request(config: pytest.Config) -> dict[str, Any] | None:
    """Which pool this session leases from: ``--qasight-resource``, else a stage's ``TESTO_RESOURCE_SPEC``."""
    pool = config.getoption("qasight_resource")
    if pool:
        return {
            "pool": pool,
            "labels": list(config.getoption("qasight_resource_label") or []),
            "per": config.getoption("qasight_resource_per"),
            "wait": config.getoption("qasight_resource_wait"),
        }
    raw = os.environ.get(_SPEC_ENV)
    if not raw:
        return None
    try:
        sent = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(sent, dict) or sent.get("per") not in ("worker", "test"):
        return None  # per: stage, the stage already holds the key
    return sent


def _is_xdist_controller(config: pytest.Config) -> bool:
    """The process that only hands tests to pytest-xdist workers (it runs none itself)."""
    return not hasattr(config, "workerinput") and getattr(config.option, "dist", "no") != "no"


def _take_key(config: pytest.Config, request: dict[str, Any]) -> Any:
    from qasight.config.errors import ConfigError
    from qasight.resources.client import acquire, backend_from_env, default_holder, find_pool
    from qasight.resources.pools import parse_duration, parse_pool
    from qasight.resources.store import LeaseError

    try:
        spec = request.get("spec")
        pool = (
            parse_pool(request["pool"], spec)
            if isinstance(spec, dict)
            else find_pool(request["pool"])
        )
        wait = request.get("wait")
        wait_s = wait if isinstance(wait, (int, float)) or wait is None else parse_duration(wait)
        return acquire(
            backend_from_env(),
            pool,
            labels=request.get("labels") or (),
            holder=default_holder(
                stage=str(request.get("stage") or ""),
                worker=os.environ.get("PYTEST_XDIST_WORKER", "main"),
                note="pytest",
            ),
            wait_s=wait_s,
        )
    except (ConfigError, LeaseError, ValueError) as exc:
        pytest.fail(f"qasight: no key from pool {request.get('pool')!r}: {exc}", pytrace=False)


def _held(config: pytest.Config, request: dict[str, Any]) -> Iterator[Any]:
    """Take a key, expose it in the environment while it is held, give it back."""
    held = _take_key(config, request)
    saved = {key: os.environ.get(key) for key in held.env()}
    os.environ.update(held.env())
    try:
        yield held
    finally:
        held.release()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session", autouse=True)
def _testo_worker_key(request: pytest.FixtureRequest) -> Iterator[Any]:
    """``per: worker``: this test process's key, taken before its first test."""
    config = request.config
    wanted = config.stash.get(_RESOURCE_REQUEST, None)
    if wanted is None or wanted.get("per") != "worker" or _is_xdist_controller(config):
        yield None
        return
    yield from _held(config, wanted)


@pytest.fixture(autouse=True)
def _testo_test_key(request: pytest.FixtureRequest) -> Iterator[Any]:
    """``per: test``: a key for this test only, so the pool's ``on_take`` runs before each test."""
    config = request.config
    wanted = config.stash.get(_RESOURCE_REQUEST, None)
    if wanted is None or wanted.get("per") != "test":
        yield None
        return
    yield from _held(config, wanted)


@pytest.fixture
def testo_resource(_testo_worker_key: Any, _testo_test_key: Any) -> Any:
    """The key this test holds: ``.id``, ``.data`` (a dict), ``.env()`` and ``.lease``."""
    held = _testo_test_key or _testo_worker_key
    if held is None:
        pytest.fail(
            "testo_resource needs a pool: run pytest with --qasight-resource POOL, or from a "
            "qasight.yaml stage with 'resources:'.",
            pytrace=False,
        )
    return held


# -- shards -----------------------------------------------------------------------


def _configure_shard(config: pytest.Config) -> None:
    raw = config.getoption("qasight_shard") or os.environ.get(_SHARD_ENV) or ""
    if not raw.strip():
        config.stash[_SHARD] = None
        return
    from qasight.services.test_split import (
        SplitError,
        load_test_times,
        parse_shard,
        timings_index,
    )

    try:
        shard = parse_shard(raw)
    except SplitError as exc:
        raise pytest.UsageError(f"--qasight-shard: {exc}") from exc
    config.stash[_SHARD] = shard
    workerinput = getattr(config, "workerinput", None)
    if workerinput is not None and _WORKER_TIMINGS in workerinput:
        # The controller's times, so every worker keeps the same tests.
        config.stash[_SHARD_TIMINGS] = dict(json.loads(workerinput[_WORKER_TIMINGS]))
        return
    cycle = _history_cycle(config)
    server = config.getoption("qasight_server") or os.environ.get("TESTO_SERVER_URL") or None
    try:
        times = load_test_times(cycle, server=server, token=os.environ.get("TESTO_API_TOKEN"))
    except Exception as exc:  # noqa: BLE001 - a shard must not guess: another shard may know
        raise pytest.UsageError(
            f"--qasight-shard: could not load the test times of cycle {cycle!r}: {exc}"
        ) from exc
    config.stash[_SHARD_TIMINGS] = timings_index(times)
    if _is_xdist_controller(config):  # the workers collect and pick the tests
        timed = sum(1 for t in times if t.median_ms is not None)
        config.stash[_SHARD_SUMMARY] = (
            f"qasight: shard {shard[0]}/{shard[1]} of cycle {cycle!r} (times known for {timed} tests)"
        )


def _history_cycle(config: pytest.Config) -> str:
    """The cycle whose recorded runs --qasight-shard and --qasight-failed-first read."""
    return (
        config.getoption("qasight_timings")
        or os.environ.get("TESTO_CYCLE")
        or config.getoption("qasight_cycle")
        or config.rootpath.name
        or "pytest"
    ).strip()


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    """pytest-xdist: hand the controller's test times and ranks to each worker."""
    timings = node.config.stash.get(_SHARD_TIMINGS, None)
    if timings is not None:
        node.workerinput[_WORKER_TIMINGS] = json.dumps(timings)
    ranks = node.config.stash.get(_FAILED_FIRST, None)
    if ranks is not None:
        node.workerinput[_WORKER_RANKS] = json.dumps(ranks)


@pytest.hookimpl(trylast=True)  # after -k / -m, so the shards split what will run
def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    _keep_affected(config, items)
    _keep_shard(config, items)
    _put_failures_first(config, items)


def _keep_shard(config: pytest.Config, items: list[pytest.Item]) -> None:
    shard = config.stash.get(_SHARD, None)
    if shard is None:
        return
    from qasight.services.test_split import nodeid_names, plan_split

    index, count = shard
    timings: dict[str, float] = (
        config.stash[_SHARD_TIMINGS] if _SHARD_TIMINGS in config.stash else {}
    )
    plan = plan_split(
        [item.nodeid for item in items],
        timings,
        count,
        names_for=nodeid_names,
    )
    mine = plan.shard(index)
    keep = set(mine.tests)
    selected = [item for item in items if item.nodeid in keep]
    deselected = [item for item in items if item.nodeid not in keep]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
    if not hasattr(config, "workerinput"):
        about = f", about {mine.estimate_ms / 1000:.1f}s" if plan.timed else ""
        config.stash[_SHARD_SUMMARY] = (
            f"qasight: shard {index}/{count} ran {len(selected)} of {plan.total} tests{about} "
            f"(times known for {plan.timed} tests)"
        )


# -- affected tests ---------------------------------------------------------------


def _configure_affected(config: pytest.Config) -> None:
    config.stash[_AFFECTED] = None
    on = config.getoption("qasight_affected") or os.environ.get(
        _AFFECTED_ENV, ""
    ).strip().lower() in ("1", "true", "yes")
    if not on:
        return
    from qasight.config.errors import ConfigDiscoveryError, ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.config.schema import Defaults, QASightConfig
    from qasight.services.affected_tests import AffectedError, compute

    try:
        try:
            cfg = discover_and_load(cwd=config.rootpath)
        except ConfigDiscoveryError:
            cfg = QASightConfig(version=1, defaults=Defaults())
        selection, root = compute(
            config.rootpath,
            cfg.affected,
            base=config.getoption("qasight_affected_base"),
            env=dict(os.environ),
        )
    except (ConfigError, AffectedError) as exc:
        raise pytest.UsageError(f"--qasight-affected: {exc}") from exc
    lines: list[str] = []
    if selection.run_all:
        lines.append("qasight: affected tests: running everything, because")
        lines.extend(f"  - {reason}" for reason in selection.run_all_reasons)
    else:
        keep: set[tuple[Path, str | None]] = set()
        for test in selection.tests:
            file_part, sep, rest = test.partition("::")
            keep.add(((root / file_part).resolve(), rest if sep else None))
        config.stash[_AFFECTED] = frozenset(keep)
        against = f" against {selection.base}" if selection.base else ""
        lines.append(
            f"qasight: affected tests: {len(selection.tests)} picked from "
            f"{len(selection.changed)} changed file(s){against}"
        )
        for test, reasons in list(selection.tests.items())[:_AFFECTED_SHOWN]:
            lines.append(f"  {test}  <- {'; '.join(reasons)}")
        if len(selection.tests) > _AFFECTED_SHOWN:
            lines.append(f"  ... and {len(selection.tests) - _AFFECTED_SHOWN} more")
        if selection.nothing_to_run:
            lines.append("  no test needs to run:")
            lines.extend(f"  - {d.path}: {d.reason}" for d in selection.files)
    if not hasattr(config, "workerinput"):
        config.stash[_AFFECTED_SUMMARY] = lines


def _matches(item: pytest.Item, file: Path, name: str | None) -> bool:
    if Path(str(item.path)).resolve() != file:
        return False
    if name is None:
        return True
    rest = item.nodeid.partition("::")[2]
    return rest == name or rest.startswith(name + "::") or rest.startswith(name + "[")


def _keep_affected(config: pytest.Config, items: list[pytest.Item]) -> None:
    keep = config.stash.get(_AFFECTED, None)
    if keep is None:
        return
    found: set[tuple[Path, str | None]] = set()
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        hits = {entry for entry in keep if _matches(item, *entry)}
        found |= hits
        (selected if hits else deselected).append(item)
    # A picked Python test that wasn't collected may have been renamed since the
    # coverage report or rule was written: its replacement is unknown, so run all.
    lost = sorted(
        f"{file.name}::{name}" if name else file.name
        for file, name in keep - found
        if file.suffix == ".py" and (name is not None or file.is_file())
    )
    summary = config.stash.get(_AFFECTED_SUMMARY, None)
    if lost:
        if summary is not None:
            summary.append(
                f"  picked but not collected ({', '.join(lost[:5])}), so running everything"
            )
        return
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
    config.stash[_AFFECTED_NOTHING] = not selected


# -- failed first ------------------------------------------------------------------


def _configure_failed_first(config: pytest.Config) -> None:
    config.stash[_FAILED_FIRST] = None
    on = config.getoption("qasight_failed_first") or os.environ.get(
        _FAILED_FIRST_ENV, ""
    ).strip().lower() in ("1", "true", "yes")
    if not on:
        return
    workerinput = getattr(config, "workerinput", None)
    if workerinput is not None and _WORKER_RANKS in workerinput:
        # The controller's ranks, so every worker collects the same order.
        config.stash[_FAILED_FIRST] = dict(json.loads(workerinput[_WORKER_RANKS]))
        return
    from qasight.services.failed_first import rank_index
    from qasight.services.test_split import load_test_items

    cycle = _history_cycle(config)
    server = config.getoption("qasight_server") or os.environ.get("TESTO_SERVER_URL") or None
    try:
        items = load_test_items(cycle, server=server, token=os.environ.get("TESTO_API_TOKEN"))
    except Exception as exc:  # noqa: BLE001 - only the order is lost; the tests still run
        config.stash[_FAILED_FIRST] = {}
        config.stash[_FAILED_FIRST_SUMMARY] = (
            f"qasight: failed first: no history for cycle {cycle!r} ({exc}); kept pytest's order"
        )
        return
    config.stash[_FAILED_FIRST] = rank_index(items)
    if not items:
        config.stash[_FAILED_FIRST_SUMMARY] = (
            f"qasight: failed first: cycle {cycle!r} has no recorded runs yet; kept pytest's order"
        )


def _put_failures_first(config: pytest.Config, items: list[pytest.Item]) -> None:
    ranks = config.stash.get(_FAILED_FIRST, None)
    if not ranks:
        return
    from qasight.services.failed_first import FAILED_LAST, FAILED_RECENTLY, FLAKY, order
    from qasight.services.test_split import nodeid_names

    positions, counts = order([item.nodeid for item in items], ranks, names_for=nodeid_names)
    items[:] = [items[i] for i in positions]
    if hasattr(config, "workerinput"):
        return
    parts = [
        f"{counts[rank]} {label}"
        for rank, label in (
            (FAILED_LAST, "failed last time"),
            (FLAKY, "flaky"),
            (FAILED_RECENTLY, "failed recently"),
        )
        if counts.get(rank)
    ]
    moved = ", ".join(parts) if parts else "none of the collected tests failed recently"
    config.stash[_FAILED_FIRST_SUMMARY] = f"qasight: failed first: {moved}"
