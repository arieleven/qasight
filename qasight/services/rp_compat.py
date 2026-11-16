"""ReportPortal-compatible reporting: point an existing ReportPortal agent at QASight.

Teams that already report with a ReportPortal agent (``pytest-reportportal``,
``@reportportal/agent-js-playwright``, the JUnit 5, TestNG, Cypress, WebdriverIO and
.NET agents, ...) switch by changing the agent's endpoint to the QASight server; the
API key is the server's ``TESTO_API_TOKEN``. The agents' reporting calls are answered
under ``/api/v1/{project}/...`` and ``/api/v2/{project}/...``
(:mod:`qasight_api.routes.rp_compat`):

- start a launch, start a suite or test (``item``, nested under a parent), finish it,
  save logs (JSON or multipart with attachments), finish the launch.

While the launch runs, each finished test is added to the "Running now" list
(:mod:`qasight_api.live_runs`). Finishing the launch turns what was reported into
Allure result files (steps, logs, attachments, attributes as labels, retries as
flaky) and records them as one run of the cycle named after the launch, through the
same path as ``POST /api/v1/ingest`` (:func:`~qasight.services.junit_ingest.ingest_report_files`).
The reported rows are then deleted; the run keeps everything.

What is reported is kept in the run-history database until the launch finishes
(tables ``rp_launch``, ``rp_item``, ``rp_log``), so a server with several workers
can take one agent's calls on any of them. A launch nobody finishes is recorded
after :data:`ABANDONED_AFTER_S` of silence by the next launch that starts.

Not answered: ReportPortal's UI API (dashboards, filters, defect updates), launch
merges and imports. Agents only report; QASight's own UI and API show the runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid as uuidlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from qasight.repository.models import RpItem, RpLaunch, RpLog

logger = logging.getLogger(__name__)

# Per-log and per-launch limits: plenty for real launches, small enough that an agent
# can't fill the database. Logs past them are counted, not stored.
MAX_LOG_CHARS: Final[int] = 64 * 1024
MAX_ATTACHMENT_BYTES: Final[int] = 20 * 1024 * 1024
MAX_LAUNCH_ATTACHMENT_BYTES: Final[int] = 200 * 1024 * 1024
ABANDONED_AFTER_S: Final[int] = 6 * 3600
STAGE: Final[str] = "reportportal"
DEFAULT_TIER: Final[str] = "e2e"

_STATUS: Final[dict[str, str]] = {
    "PASSED": "passed",
    "FAILED": "failed",
    "INTERRUPTED": "failed",
    "STOPPED": "failed",
    "SKIPPED": "skipped",
    "CANCELLED": "skipped",
    "INFO": "passed",
    "WARN": "passed",
}
_LIVE_OUTCOME: Final[dict[str, str]] = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
}
_HOOK_TYPES: Final[frozenset[str]] = frozenset(
    {
        "BEFORE_CLASS",
        "BEFORE_GROUPS",
        "BEFORE_METHOD",
        "BEFORE_SUITE",
        "BEFORE_TEST",
        "AFTER_CLASS",
        "AFTER_GROUPS",
        "AFTER_METHOD",
        "AFTER_SUITE",
        "AFTER_TEST",
    }
)
_ERROR_LEVELS: Final[frozenset[str]] = frozenset({"ERROR", "FATAL"})
_UNSAFE_CYCLE = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")
# Launch attribute keys that say what a run tested on, kept as its environment.
_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {"browser", "os", "platform", "device", "env", "environment", "python", "node", "java", "jdk"}
)


class RpError(ValueError):
    """A request the compatible endpoint can't take: unknown launch or item, bad body."""

    def __init__(self, message: str, *, status: int = 404, code: int = 40422) -> None:
        super().__init__(message)
        self.status = status
        # ReportPortal's own error codes, which agents print: 4041x not found, 4001 bad request.
        self.code = code


# --------------------------------------------------------------------------- parsing


def parse_time_ms(value: Any, *, default: int | None = None) -> int:
    """Epoch milliseconds from what agents send: a number, digits, or an ISO 8601 string.

    Older agents send ``1700000000000``; newer ones ``"2026-11-16T10:00:00.123456Z"``
    (microseconds) or ``"2026-11-16T10:00:00.123+02:00"``.
    """
    fallback = default if default is not None else int(time.time() * 1000)
    if value is None or value == "":
        return fallback
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int | float):
        number = float(value)
        # Seconds, not milliseconds, from a hand-written client.
        return int(number * 1000) if number < 10_000_000_000 else int(number)
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return parse_time_ms(float(text), default=fallback)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def cycle_for_launch(name: str) -> str:
    """A cycle name from a launch name: ``"Smoke Tests"`` -> ``"Smoke-Tests"``."""
    slug = _UNSAFE_CYCLE.sub("-", name.strip()).strip("-._")[:128]
    if not slug:
        return "reportportal"
    if not slug[0].isalnum():
        slug = f"launch-{slug}"[:128]
    return slug


def _attributes(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for attr in raw or []:
        if not isinstance(attr, Mapping):
            continue
        value = attr.get("value")
        if value is None or str(value).strip() == "":
            continue
        out.append(
            {
                "key": str(attr.get("key"))[:255] if attr.get("key") not in (None, "") else None,
                "value": str(value)[:255],
                "system": bool(attr.get("system")),
            }
        )
    return out[:100]


def _parameters(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for param in raw or []:
        if isinstance(param, Mapping):
            out.append(
                {
                    "name": str(param.get("key") or "")[:255],
                    "value": str(param.get("value") or "")[:1000],
                }
            )
    return out[:100]


def _text(value: Any, limit: int) -> str:
    return "" if value is None else str(value)[:limit]


# --------------------------------------------------------------------------- store


@dataclass(frozen=True)
class FinishedLaunch:
    uuid: str
    number: int
    cycle: str
    run_id: str | None
    tests: int
    failed: int


class RpStore:
    """What ReportPortal agents report, kept until the launch finishes."""

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine
        self._ready: set[tuple[int, str]] = set()

    def _db(self) -> Any:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        engine = self._engine if self._engine is not None else get_engine()
        key = (id(engine), str(engine.url))
        if key not in self._ready:
            create_db_and_tables(engine)
            self._ready.add(key)
        return engine

    # ---- launches

    def start_launch(self, project: str, body: Mapping[str, Any]) -> RpLaunch:
        from qasight.services.live_sessions import live_runs

        name = _text(body.get("name"), 512).strip() or "launch"
        self.record_abandoned()
        launch_uuid = _text(body.get("uuid"), 64).strip() or str(uuidlib.uuid4())
        with Session(self._db()) as db:
            existing = db.get(RpLaunch, launch_uuid)
            if existing is not None:
                # An agent retrying its start call (or a rerun of an open launch) gets the same launch.
                return existing
            number = (
                db.exec(
                    select(func.count()).select_from(RpLaunch).where(RpLaunch.project == project)
                ).one()
                or 0
            ) + 1
            attributes = _attributes(body.get("attributes"))
            cycle = cycle_for_launch(name)
            values = {a["key"]: a["value"] for a in attributes if a["key"] and not a["system"]}
            session = live_runs.start(
                cycle,
                branch=values.get("branch"),
                commit=values.get("commit") or values.get("sha"),
                source="reportportal",
            )
            launch = RpLaunch(
                uuid=launch_uuid,
                project=project[:255],
                name=name,
                cycle=cycle,
                number=int(number),
                description=_text(body.get("description"), 4000),
                mode=_text(body.get("mode") or "DEFAULT", 16),
                attributes=attributes,
                start_ms=parse_time_ms(body.get("startTime")),
                live_session_id=session.id,
            )
            db.add(launch)
            db.commit()
            db.refresh(launch)
            return launch

    def get_launch(self, launch_uuid: str) -> RpLaunch:
        with Session(self._db()) as db:
            launch = db.get(RpLaunch, launch_uuid)
            if launch is None:
                raise RpError(f"Launch '{launch_uuid}' not found.", code=40422)
            return launch

    def _open_launch(self, db: Session, launch_uuid: str) -> RpLaunch:
        launch = db.get(RpLaunch, launch_uuid)
        if launch is None:
            raise RpError(f"Launch '{launch_uuid}' not found.", code=40422)
        if launch.status != "IN_PROGRESS":
            raise RpError(f"Launch '{launch_uuid}' is already finished.", status=406, code=40061)
        return launch

    # ---- items

    def start_item(
        self, project: str, body: Mapping[str, Any], parent_uuid: str | None = None
    ) -> RpItem:
        launch_uuid = _text(body.get("launchUuid") or body.get("launch_uuid"), 64)
        if not launch_uuid:
            raise RpError("launchUuid is required.", status=400, code=4001)
        item_uuid = _text(body.get("uuid"), 64).strip() or str(uuidlib.uuid4())
        with Session(self._db()) as db:
            launch = self._open_launch(db, launch_uuid)
            if parent_uuid is not None and db.get(RpItem, parent_uuid) is None:
                raise RpError(f"Test Item '{parent_uuid}' not found.", code=40422)
            existing = db.get(RpItem, item_uuid)
            if existing is not None:
                return existing
            has_stats = body.get("hasStats")
            item = RpItem(
                uuid=item_uuid,
                launch_uuid=launch.uuid,
                parent_uuid=parent_uuid,
                name=_text(body.get("name"), 1024).strip() or "(unnamed)",
                type=_text(body.get("type") or "TEST", 32).upper(),
                has_stats=True if has_stats is None else bool(has_stats),
                code_ref=_text(body.get("codeRef"), 1024),
                test_case_id=_text(body.get("testCaseId") or body.get("uniqueId"), 1024),
                description=_text(body.get("description"), 4000),
                attributes=_attributes(body.get("attributes")),
                parameters=_parameters(body.get("parameters")),
                start_ms=parse_time_ms(body.get("startTime")),
                retry=bool(body.get("retry")),
                seq=time.time_ns(),
            )
            db.add(item)
            launch.updated_at = datetime.now(tz=UTC)
            db.add(launch)
            db.commit()
            db.refresh(item)
            return item

    def finish_item(self, project: str, item_uuid: str, body: Mapping[str, Any]) -> RpItem:
        from qasight.services.live_sessions import live_runs

        with Session(self._db()) as db:
            item = db.get(RpItem, item_uuid)
            if item is None:
                raise RpError(f"Test Item '{item_uuid}' not found.", code=40422)
            launch = self._open_launch(db, item.launch_uuid)
            item.end_ms = parse_time_ms(body.get("endTime"))
            raw_status = _text(body.get("status"), 16).upper()
            children = db.exec(select(RpItem).where(RpItem.parent_uuid == item.uuid)).all()
            if raw_status:
                item.status = raw_status
            else:
                item.status = _rolled_up_status(children)
            if body.get("retry") is not None:
                item.retry = bool(body.get("retry"))
            issue = body.get("issue")
            if isinstance(issue, Mapping) and issue.get("issueType"):
                item.issue_type = _text(issue.get("issueType"), 32)
            extra = _attributes(body.get("attributes"))
            if extra:
                item.attributes = [*item.attributes, *extra][:100]
            if body.get("description"):
                item.description = _text(body.get("description"), 4000)
            db.add(item)
            launch.updated_at = datetime.now(tz=UTC)
            db.add(launch)
            db.commit()
            db.refresh(item)
            is_test = item.has_stats and not any(c.has_stats for c in children)
            if is_test and launch.live_session_id and _counts_as_test(item):
                outcome = _LIVE_OUTCOME.get(_STATUS.get(item.status or "", "passed"), "passed")
                message = None
                if outcome == "failed":
                    message = self._first_error(db, item.uuid)
                live_runs.add_results(
                    launch.live_session_id, [(_display_name(db, item), outcome, message)]
                )
            return item

    @staticmethod
    def _first_error(db: Session, item_uuid: str) -> str | None:
        row = db.exec(
            select(RpLog)
            .where(RpLog.item_uuid == item_uuid)
            .where(RpLog.level.in_(_ERROR_LEVELS))  # type: ignore[attr-defined]
            .order_by(RpLog.time_ms)  # type: ignore[arg-type]
            .limit(1)
        ).first()
        return row.message if row is not None else None

    # ---- logs

    def save_logs(
        self,
        project: str,
        entries: Sequence[Mapping[str, Any]],
        files: Mapping[str, tuple[bytes, str | None]] | None = None,
    ) -> list[str]:
        """Store log entries; an entry's ``file.name`` picks its attachment from ``files``."""
        files = files or {}
        ids: list[str] = []
        with Session(self._db()) as db:
            launches: dict[str, RpLaunch] = {}
            stored_bytes: dict[str, int] = {}
            for entry in entries:
                launch_uuid = _text(entry.get("launchUuid") or entry.get("launch_uuid"), 64)
                item_uuid = _text(entry.get("itemUuid") or entry.get("item_uuid"), 64) or None
                if not launch_uuid and item_uuid:
                    item = db.get(RpItem, item_uuid)
                    launch_uuid = item.launch_uuid if item is not None else ""
                if launch_uuid not in launches:
                    launches[launch_uuid] = self._open_launch(db, launch_uuid)
                    stored_bytes[launch_uuid] = _attachment_bytes(db, launch_uuid)
                log_uuid = _text(entry.get("uuid"), 64).strip() or str(uuidlib.uuid4())
                log = RpLog(
                    uuid=log_uuid,
                    launch_uuid=launch_uuid,
                    item_uuid=item_uuid,
                    time_ms=parse_time_ms(entry.get("time")),
                    level=_text(entry.get("level") or "INFO", 16).upper(),
                    message=_text(entry.get("message"), MAX_LOG_CHARS),
                )
                spec = entry.get("file")
                if isinstance(spec, Mapping) and spec.get("name"):
                    name = str(spec["name"])
                    found = files.get(name)
                    if found is not None:
                        data, content_type = found
                        size = len(data)
                        if (
                            size <= MAX_ATTACHMENT_BYTES
                            and stored_bytes[launch_uuid] + size <= MAX_LAUNCH_ATTACHMENT_BYTES
                        ):
                            log.attachment = data
                            log.attachment_name = Path(name).name[:255] or "attachment"
                            log.attachment_type = (
                                content_type
                                or spec.get("contentType")
                                or "application/octet-stream"
                            )[:128]
                            stored_bytes[launch_uuid] += size
                        else:
                            log.message = (
                                f"{log.message}\n[attachment {name!r} left out: over the size limit]"
                            ).strip()[:MAX_LOG_CHARS]
                if db.get(RpLog, log_uuid) is None:
                    db.add(log)
                ids.append(log_uuid)
            for launch in launches.values():
                launch.updated_at = datetime.now(tz=UTC)
                db.add(launch)
            db.commit()
        return ids

    # ---- finishing

    def finish_launch(
        self, project: str, launch_uuid: str, body: Mapping[str, Any] | None = None
    ) -> FinishedLaunch:
        with Session(self._db()) as db:
            launch = db.get(RpLaunch, launch_uuid)
            if launch is None:
                raise RpError(f"Launch '{launch_uuid}' not found.", code=40422)
            if launch.status != "IN_PROGRESS":
                return FinishedLaunch(
                    uuid=launch.uuid,
                    number=launch.number,
                    cycle=launch.cycle,
                    run_id=launch.run_id,
                    tests=0,
                    failed=0,
                )
            launch.status = "FINISHING"
            launch.end_ms = parse_time_ms((body or {}).get("endTime"))
            db.add(launch)
            db.commit()
            db.refresh(launch)
        return self._record(launch)

    def record_abandoned(self, *, now: datetime | None = None) -> int:
        """Record launches nobody finished for :data:`ABANDONED_AFTER_S`; returns how many."""
        cutoff = (now or datetime.now(tz=UTC)) - timedelta(seconds=ABANDONED_AFTER_S)
        with Session(self._db()) as db:
            stale = db.exec(
                select(RpLaunch)
                .where(RpLaunch.status == "IN_PROGRESS")
                .where(RpLaunch.updated_at < cutoff)
            ).all()
            # updated_at may come back naive (SQLite) or aware (PostgreSQL); compare both ways.
            stale = [s for s in stale if _aware(s.updated_at) < cutoff]
        recorded = 0
        for launch in stale:
            try:
                self.finish_launch(launch.project, launch.uuid, {})
                recorded += 1
            except Exception:
                logger.warning("could not record abandoned launch %s", launch.uuid, exc_info=True)
        return recorded

    def _record(self, launch: RpLaunch) -> FinishedLaunch:
        from qasight.config.loader import load_config_or_defaults
        from qasight.services.junit_ingest import clean_run_metadata, ingest_report_files
        from qasight.services.live_sessions import live_runs

        with Session(self._db()) as db:
            items = db.exec(
                select(RpItem).where(RpItem.launch_uuid == launch.uuid).order_by(RpItem.seq)  # type: ignore[arg-type]
            ).all()
            logs = db.exec(
                select(RpLog).where(RpLog.launch_uuid == launch.uuid).order_by(RpLog.time_ms)  # type: ignore[arg-type]
            ).all()
            built = build_results(launch, list(items), list(logs))

        metadata = clean_run_metadata(launch_metadata(launch))
        tier = _attr(launch.attributes, "tier") or DEFAULT_TIER
        if tier not in ("unit", "integration", "e2e"):
            tier = DEFAULT_TIER

        def write(reports_dir: Path) -> None:
            for result in built.results:
                (reports_dir / f"{result['uuid']}-result.json").write_text(
                    json.dumps(result), encoding="utf-8"
                )
            for name, data in built.files.items():
                (reports_dir / name).write_bytes(data)

        run_id: str | None = None
        if built.results:
            outcome = ingest_report_files(
                cfg=load_config_or_defaults(),
                cycle=launch.cycle,
                write=write,
                stage=STAGE,
                tier=tier,
                run_metadata=metadata,
            )
            run_id = outcome.run_id
        with Session(self._db()) as db:
            row = db.get(RpLaunch, launch.uuid)
            if row is not None:
                row.status = "FINISHED"
                row.run_id = run_id
                row.updated_at = datetime.now(tz=UTC)
                db.add(row)
            # The run has everything now; the reported rows only took space.
            db.execute(delete(RpLog).where(col(RpLog.launch_uuid) == launch.uuid))
            db.execute(delete(RpItem).where(col(RpItem.launch_uuid) == launch.uuid))
            db.commit()
        if launch.live_session_id:
            if built.results:
                live_runs.set_total(launch.live_session_id, len(built.results))
            live_runs.finish(launch.live_session_id)
        return FinishedLaunch(
            uuid=launch.uuid,
            number=launch.number,
            cycle=launch.cycle,
            run_id=run_id,
            tests=len(built.results),
            failed=sum(1 for r in built.results if r["status"] in ("failed", "broken")),
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _attachment_bytes(db: Session, launch_uuid: str) -> int:
    total = db.exec(
        select(func.coalesce(func.sum(func.length(RpLog.attachment)), 0)).where(
            RpLog.launch_uuid == launch_uuid
        )
    ).one()
    return int(total or 0)


def _rolled_up_status(children: Iterable[RpItem]) -> str:
    statuses = [c.status for c in children if c.status and not c.retry]
    if any(_STATUS.get(s) == "failed" for s in statuses):
        return "FAILED"
    if statuses and all(_STATUS.get(s) == "skipped" for s in statuses):
        return "SKIPPED"
    return "PASSED"


def _counts_as_test(item: RpItem) -> bool:
    # A passing before/after hook is not a test; a failing one is shown, because it broke the tests.
    return item.type not in _HOOK_TYPES or _STATUS.get(item.status or "") == "failed"


def _display_name(db: Session, item: RpItem) -> str:
    names = [item.name]
    parent = item.parent_uuid
    depth = 0
    while parent and depth < 10:
        row = db.get(RpItem, parent)
        if row is None:
            break
        names.append(row.name)
        parent = row.parent_uuid
        depth += 1
    return " > ".join(reversed(names))[:1000]


def _attr(attributes: Iterable[Mapping[str, Any]], key: str) -> str | None:
    for attr in attributes:
        if attr.get("key") == key and not attr.get("system"):
            return str(attr.get("value"))
    return None


def launch_metadata(launch: RpLaunch) -> dict[str, str | None]:
    """Run-record fields a launch's attributes carry: branch, commit, environment, release."""
    attrs = [a for a in launch.attributes if not a.get("system")]
    env = ", ".join(
        f"{a['key']}={a['value']}" for a in attrs if a.get("key") and a["key"].lower() in _ENV_KEYS
    )
    return {
        "ci_provider": "reportportal-agent",
        "ci_ref_name": _attr(attrs, "branch"),
        "ci_commit_sha": _attr(attrs, "commit") or _attr(attrs, "sha"),
        "repository": _attr(attrs, "repository") or _attr(attrs, "repo"),
        "environment": env or None,
        "release": _attr(attrs, "release") or _attr(attrs, "version"),
    }


# --------------------------------------------------------------------------- building results


@dataclass
class BuiltResults:
    results: list[dict[str, Any]] = field(default_factory=list)
    # attachment file name -> content, written next to the results.
    files: dict[str, bytes] = field(default_factory=dict)


def build_results(launch: RpLaunch, items: list[RpItem], logs: list[RpLog]) -> BuiltResults:
    """Allure ``*-result.json`` dicts (and their attachment files) for a reported launch.

    A test is an item with stats and no children with stats; its nested steps
    (``hasStats: false``) become Allure steps. Its containers name the suites. An
    attempt followed by a retry of the same test is folded into the last attempt,
    marked flaky when an earlier attempt failed and the last one passed.
    """
    built = BuiltResults()
    by_uuid = {i.uuid: i for i in items}
    children: dict[str | None, list[RpItem]] = {}
    for item in items:
        children.setdefault(item.parent_uuid, []).append(item)
    logs_by_item: dict[str | None, list[RpLog]] = {}
    for log in logs:
        logs_by_item.setdefault(log.item_uuid, []).append(log)
    agent = next(
        (a["value"] for a in launch.attributes if a.get("system") and a.get("key") == "agent"), None
    )
    framework = (agent or "reportportal").split("|", 1)[0][:100]

    def ancestors(item: RpItem) -> list[RpItem]:
        chain: list[RpItem] = []
        parent = item.parent_uuid
        while parent and parent in by_uuid and len(chain) < 20:
            chain.append(by_uuid[parent])
            parent = by_uuid[parent].parent_uuid
        return list(reversed(chain))

    def attach(log: RpLog) -> dict[str, str] | None:
        if log.attachment is None:
            return None
        suffix = Path(log.attachment_name or "").suffix.lower()
        name = f"{uuidlib.uuid4()}-attachment{suffix if _SAFE_SUFFIX.fullmatch(suffix) else ''}"
        built.files[name] = log.attachment
        return {
            "name": log.attachment_name or "attachment",
            "source": name,
            "type": log.attachment_type or "application/octet-stream",
        }

    def log_attachments(item_logs: list[RpLog]) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        lines: list[str] = []
        for log in item_logs:
            if log.message:
                stamp = datetime.fromtimestamp(log.time_ms / 1000, tz=UTC).strftime("%H:%M:%S.%f")[
                    :-3
                ]
                lines.append(f"{stamp} {log.level:<5} {log.message}")
            made = attach(log)
            if made is not None:
                found.append(made)
        if lines:
            name = f"{uuidlib.uuid4()}-attachment.txt"
            built.files[name] = "\n".join(lines).encode("utf-8")
            found.insert(0, {"name": "log", "source": name, "type": "text/plain"})
        return found

    def step(node: RpItem) -> dict[str, Any]:
        status = _STATUS.get(node.status or "", "passed")
        return {
            "name": node.name,
            "status": status,
            "stage": "finished",
            "start": node.start_ms,
            "stop": node.end_ms or node.start_ms,
            "steps": [step(c) for c in steps_of(node.uuid)],
            "attachments": log_attachments(logs_by_item.get(node.uuid, [])),
        }

    def steps_of(parent: str) -> list[RpItem]:
        # Agents report asynchronously, so arrival order isn't step order; start time is.
        return sorted(
            (c for c in children.get(parent, []) if not c.has_stats),
            key=lambda c: (c.start_ms, c.seq),
        )

    def error_logs(item_uuid: str) -> list[RpLog]:
        """The item's error logs, else its steps' (Playwright and Cypress log the failure on the step)."""
        found = [
            g for g in logs_by_item.get(item_uuid, []) if g.level in _ERROR_LEVELS and g.message
        ]
        if found:
            return found
        for child in steps_of(item_uuid):
            found = error_logs(child.uuid)
            if found:
                return found
        return []

    def is_test(item: RpItem) -> bool:
        return item.has_stats and not any(c.has_stats for c in children.get(item.uuid, []))

    tests = [i for i in items if is_test(i) and _counts_as_test(i)]
    # Group attempts of one test: same parent and same identity, in reporting order.
    groups: dict[tuple[str | None, str], list[RpItem]] = {}
    for item in tests:
        identity = item.test_case_id or item.code_ref or item.name
        params = json.dumps(item.parameters, sort_keys=True)
        groups.setdefault((item.parent_uuid, f"{identity}\n{params}"), []).append(item)

    for attempts in groups.values():
        last = attempts[-1]
        earlier_failed = [a for a in attempts[:-1] if _STATUS.get(a.status or "") == "failed"]
        status = _STATUS.get(last.status or "", "passed")
        chain = ancestors(last)
        suites = [c.name for c in chain]
        item_logs = logs_by_item.get(last.uuid, [])
        errors = error_logs(last.uuid)
        full_name = last.code_ref or ".".join([*suites, last.name])
        if last.parameters:
            full_name += "[" + ", ".join(f"{p['name']}={p['value']}" for p in last.parameters) + "]"
        identity = last.test_case_id or full_name
        labels: list[dict[str, str]] = [{"name": "framework", "value": framework}]
        for label, value in zip(
            ("parentSuite", "suite", "subSuite"), _suite_levels(suites), strict=False
        ):
            if value:
                labels.append({"name": label, "value": value})
        for attr in last.attributes:
            if attr.get("system"):
                continue
            if attr.get("key"):
                labels.append({"name": str(attr["key"]), "value": str(attr["value"])})
            else:
                labels.append({"name": "tag", "value": str(attr["value"])})
        result: dict[str, Any] = {
            "uuid": str(uuidlib.uuid4()),
            "historyId": hashlib.sha256(f"reportportal:{identity}".encode()).hexdigest(),
            "name": last.name,
            "fullName": full_name,
            "status": status,
            "stage": "finished",
            "start": last.start_ms,
            "stop": max(last.end_ms or last.start_ms, last.start_ms),
            "labels": [dict(t) for t in dict.fromkeys(tuple(lab.items()) for lab in labels)],
            "steps": [step(c) for c in steps_of(last.uuid)],
            "attachments": log_attachments(item_logs),
        }
        if last.description:
            result["description"] = last.description
        if last.parameters:
            result["parameters"] = last.parameters
        details: dict[str, Any] = {}
        if errors:
            text = errors[0].message.strip()
            details["message"] = failure_message(text)[:2000]
            details["trace"] = text[:MAX_LOG_CHARS]
        if earlier_failed and status == "passed":
            details["flaky"] = True
            first = error_logs(earlier_failed[0].uuid)
            why = next(
                (failure_message(log.message) for log in first if log.level in _ERROR_LEVELS), ""
            )
            details["message"] = f"Passed on retry. {why}".strip()[:2000]
        if details:
            result["statusDetails"] = details
        built.results.append(result)
    return built


_PYTEST_E_LINE = re.compile(r"^E\s+(.+)$")
_EXCEPTION_LINE = re.compile(r"^\s*([\w.$]+(Error|Exception|Failure|Failed|Fault)\b.*)$")


def failure_message(text: str) -> str:
    """The line of an error log that says what went wrong, not where.

    Agents log the whole traceback. pytest's ``E   AssertionError: ...`` line wins;
    then the last ``SomethingError: ...`` line (Python puts it last, Java and
    JavaScript first, so the first is taken when it opens the text); then the first line.
    """
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    for line in lines:
        match = _PYTEST_E_LINE.match(line)
        if match:
            return match.group(1).strip()
    if _EXCEPTION_LINE.match(lines[0]):
        return lines[0].strip()
    for line in reversed(lines):
        if _EXCEPTION_LINE.match(line):
            return line.strip()
    return lines[0].strip()


def _suite_levels(suites: list[str]) -> list[str]:
    """Allure's three suite levels from any depth: outermost, next, and the rest joined."""
    if len(suites) <= 3:
        return suites
    return [suites[0], suites[1], " > ".join(suites[2:])]


rp_store = RpStore()
