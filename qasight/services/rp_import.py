"""Import a ReportPortal project's history: ``qasight import reportportal``.

Moving from ReportPortal shouldn't cost a team its history or the failure
decisions it made there. This reads past launches through ReportPortal's API
(an API key with read access is enough) and records each one as a run of the
cycle named after the launch, dated when it ran, through the same path as a
launch reported live (:func:`qasight.services.rp_compat.build_results`).

The defect type someone gave a failure in ReportPortal becomes its cause in
QASight (:mod:`qasight.services.triage`), with the comment as the note, so later
failures with the same message inherit it on day one:

    pb* (Product Bug)        -> product_bug
    ab* (Automation Bug)     -> test_bug
    si* (System Issue)       -> environment
    nd* (No Defect)          -> other, noted "No defect in ReportPortal"
    ti* (To Investigate)     -> nothing (nobody decided yet)

Linked tickets (``externalSystemIssues``) become issue links on the test. A launch
already imported (its ReportPortal uuid is on a run) is skipped, so the command
can run again to pick up new launches.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import requests

from qasight.repository.models import RpItem, RpLaunch, RpLog
from qasight.services.rp_compat import (
    BuiltResults,
    _attributes,
    _parameters,
    build_results,
    cycle_for_launch,
    failure_message,
    launch_metadata,
    parse_time_ms,
)

logger = logging.getLogger(__name__)

PAGE_SIZE: Final[int] = 300
MAX_ERROR_LOGS_PER_LAUNCH: Final[int] = 500
IMPORTED_KEY: Final[str] = "reportportal_launch_uuid"
CAUSE_BY_PREFIX: Final[dict[str, str]] = {
    "pb": "product_bug",
    "ab": "test_bug",
    "si": "environment",
    "nd": "other",
}
IMPORTER: Final[str] = "reportportal-import"


class RpImportError(RuntimeError):
    """ReportPortal refused or failed a request."""


def cause_for_issue(issue_type: str | None) -> str | None:
    """The QASight cause for a ReportPortal defect type locator (``pb001``, ``ab_xyz``, ...)."""
    if not issue_type:
        return None
    return CAUSE_BY_PREFIX.get(issue_type.strip().lower()[:2])


class RpClient:
    """Read-only calls to a ReportPortal server's API v1."""

    def __init__(
        self,
        *,
        url: str,
        project: str,
        token: str,
        session: requests.Session | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base = f"{url.rstrip('/')}/api/v1/{project}"
        self.project = project
        self._session = session or requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"
        self._timeout = timeout

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        try:
            resp = self._session.get(
                f"{self.base}/{path}", params=dict(params), timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise RpImportError(f"could not reach ReportPortal: {exc}") from exc
        if resp.status_code == 401:
            raise RpImportError("ReportPortal refused the API key (401)")
        if resp.status_code == 403:
            raise RpImportError(f"the API key can't read project {self.project!r} (403)")
        if resp.status_code == 404:
            raise RpImportError(f"ReportPortal has no project {self.project!r} (404)")
        if resp.status_code >= 400:
            raise RpImportError(f"ReportPortal answered HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def _pages(
        self, path: str, params: Mapping[str, Any], limit: int | None = None
    ) -> Iterator[dict]:
        page = 1
        seen = 0
        while True:
            body = self._get(path, {**params, "page.page": page, "page.size": PAGE_SIZE})
            content = body.get("content") or []
            for row in content:
                if isinstance(row, dict):
                    yield row
                    seen += 1
                    if limit is not None and seen >= limit:
                        return
            info = body.get("page") or {}
            if not content or page >= int(info.get("totalPages") or page):
                return
            page += 1

    def launches(self, *, limit: int, name: str | None = None) -> list[dict]:
        """Finished launches, newest first, at most *limit*."""
        params: dict[str, Any] = {"page.sort": "startTime,desc", "filter.ne.status": "IN_PROGRESS"}
        if name:
            params["filter.eq.name"] = name
        return list(self._pages("launch", params, limit))

    def items(self, launch_id: int | str) -> list[dict]:
        return list(self._pages("item", {"filter.eq.launchId": launch_id, "isLatest": "false"}))

    def first_error(self, item_id: int | str) -> str | None:
        body = self._get(
            "log",
            {
                "filter.eq.item": item_id,
                "filter.gte.level": "ERROR",
                "page.size": 1,
                "page.sort": "logTime,asc",
            },
        )
        for row in body.get("content") or []:
            if isinstance(row, dict) and row.get("message"):
                return str(row["message"])
        return None


@dataclass
class ImportReport:
    imported: list[tuple[str, str]] = field(default_factory=list)  # (launch name #n, run id)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    causes: int = 0


def _label(launch: Mapping[str, Any]) -> str:
    return f"{launch.get('name')} #{launch.get('number')}"


def convert_launch(
    client: RpClient, launch: Mapping[str, Any]
) -> tuple[RpLaunch, BuiltResults, list[tuple[str, str, str]]]:
    """A launch as recorded results, and ``(message, cause, note)`` for its decided failures."""
    name = str(launch.get("name") or "launch")
    row = RpLaunch(
        uuid=str(launch.get("uuid") or launch.get("id")),
        project=client.project,
        name=name,
        cycle=cycle_for_launch(name),
        number=int(launch.get("number") or 1),
        description=str(launch.get("description") or "")[:4000],
        attributes=_attributes(launch.get("attributes")),
        start_ms=parse_time_ms(launch.get("startTime")),
        end_ms=parse_time_ms(launch.get("endTime"), default=parse_time_ms(launch.get("startTime"))),
        status="FINISHED",
    )
    raw_items = client.items(launch["id"])
    items: list[RpItem] = []
    logs: list[RpLog] = []
    links: dict[str, list[dict[str, str]]] = {}
    decisions: list[tuple[str, str, str]] = []
    children = {str(i.get("parent")) for i in raw_items if i.get("parent") is not None}
    error_budget = MAX_ERROR_LOGS_PER_LAUNCH

    def add(raw: Mapping[str, Any], seq: int, *, retry: bool = False) -> RpItem:
        item = RpItem(
            uuid=str(raw.get("id")),
            launch_uuid=row.uuid,
            parent_uuid=str(raw["parent"]) if raw.get("parent") is not None else None,
            name=str(raw.get("name") or "(unnamed)")[:1024],
            type=str(raw.get("type") or "TEST").upper()[:32],
            has_stats=bool(raw.get("hasStats", True)),
            code_ref=str(raw.get("codeRef") or "")[:1024],
            test_case_id=str(raw.get("testCaseId") or raw.get("uniqueId") or "")[:1024],
            description=str(raw.get("description") or "")[:4000],
            attributes=_attributes(raw.get("attributes")),
            parameters=_parameters(raw.get("parameters")),
            start_ms=parse_time_ms(raw.get("startTime")),
            end_ms=parse_time_ms(raw.get("endTime"), default=parse_time_ms(raw.get("startTime"))),
            status=str(raw.get("status") or "PASSED").upper()[:16],
            retry=retry,
            seq=seq,
        )
        items.append(item)
        return item

    seq = 0
    for raw in sorted(raw_items, key=lambda r: parse_time_ms(r.get("startTime"), default=0)):
        # Earlier attempts first, as an agent reports them, so the fold marks the test flaky.
        for attempt in raw.get("retries") or []:
            if isinstance(attempt, dict):
                seq += 1
                add({**attempt, "parent": raw.get("parent")}, seq)
        seq += 1
        item = add(raw, seq, retry=bool(raw.get("retries")))
        is_test = item.has_stats and item.uuid not in children
        if not is_test or item.status not in ("FAILED", "INTERRUPTED"):
            continue
        message: str | None = None
        if error_budget > 0:
            error_budget -= 1
            try:
                message = client.first_error(raw["id"])
            except RpImportError as exc:
                logger.info("no logs for item %s: %s", raw.get("id"), exc)
        if message:
            logs.append(
                RpLog(
                    uuid=f"log-{item.uuid}",
                    launch_uuid=row.uuid,
                    item_uuid=item.uuid,
                    time_ms=item.start_ms,
                    level="ERROR",
                    message=message[:65536],
                )
            )
        raw_issue = raw.get("issue")
        issue: dict[str, Any] = raw_issue if isinstance(raw_issue, dict) else {}
        tickets = [
            {"name": str(t.get("ticketId") or t.get("url")), "url": str(t["url"]), "type": "issue"}
            for t in issue.get("externalSystemIssues") or []
            if isinstance(t, dict) and t.get("url")
        ]
        if tickets:
            links[item.uuid] = tickets
        cause = cause_for_issue(issue.get("issueType"))
        if cause and message:
            note = str(issue.get("comment") or "").strip()
            if cause == "other" and not note:
                note = "No defect in ReportPortal"
            decisions.append((failure_message(message), cause, note))
    return row, build_results(row, items, logs, links=links), decisions


def imported_launches(cycle: str) -> set[str]:
    from qasight.repository.db import get_repository

    rows = get_repository().list_runs(cycle=cycle, limit=5000)
    return {
        str((r.metadata_ or {}).get(IMPORTED_KEY))
        for r in rows
        if (r.metadata_ or {}).get(IMPORTED_KEY)
    }


def _backdate(run_id: str, launch: RpLaunch) -> None:
    from qasight.repository.db import get_repository

    repo = get_repository()
    record = repo.get_run(run_id)
    if record is None:
        return
    start = datetime.fromtimestamp(launch.start_ms / 1000, tz=UTC)
    end = datetime.fromtimestamp((launch.end_ms or launch.start_ms) / 1000, tz=UTC)
    record.start_time = start
    record.end_time = end
    meta = dict(record.metadata_ or {})
    meta.update(
        {
            "started_at": start.timestamp(),
            "finished_at": end.timestamp(),
            "started_at_iso": start.isoformat(),
            "backfilled_from": "reportportal",
            IMPORTED_KEY: launch.uuid,
        }
    )
    record.metadata_ = meta
    repo.bulk_update([record])


def import_project(
    client: RpClient,
    *,
    limit: int = 100,
    name: str | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> ImportReport:
    """Record up to *limit* of the project's latest finished launches, oldest first."""
    import json
    from pathlib import Path

    from qasight.config.loader import load_config_or_defaults
    from qasight.services.junit_ingest import clean_run_metadata, ingest_report_files
    from qasight.services.rp_compat import DEFAULT_TIER, STAGE
    from qasight.services.triage import TriageError, TriageStore

    report = ImportReport()
    cfg = load_config_or_defaults()
    triage = TriageStore()
    known: dict[str, set[str]] = {}
    for launch in reversed(client.launches(limit=limit, name=name)):
        label = _label(launch)
        cycle = cycle_for_launch(str(launch.get("name") or "launch"))
        if cycle not in known:
            known[cycle] = imported_launches(cycle)
        if str(launch.get("uuid")) in known[cycle]:
            report.skipped.append(label)
            continue
        try:
            row, built, decisions = convert_launch(client, launch)
        except RpImportError as exc:
            report.failed.append((label, str(exc)))
            continue
        if not built.results:
            report.skipped.append(f"{label} (no tests)")
            continue

        def write(reports_dir: Path, built: BuiltResults = built) -> None:
            for result in built.results:
                (reports_dir / f"{result['uuid']}-result.json").write_text(
                    json.dumps(result), encoding="utf-8"
                )
            for file_name, data in built.files.items():
                (reports_dir / file_name).write_bytes(data)

        outcome = ingest_report_files(
            cfg=cfg,
            cycle=row.cycle,
            write=write,
            stage=STAGE,
            tier=DEFAULT_TIER,
            run_metadata=clean_run_metadata(launch_metadata(row) | {"ci_provider": "reportportal"}),
        )
        if outcome.run_id:
            _backdate(outcome.run_id, row)
            report.imported.append((label, outcome.run_id))
            known[cycle].add(row.uuid)
        for message, cause, note in decisions:
            try:
                triage.mark(
                    cycle=row.cycle,
                    message=message,
                    cause=cause,
                    note=note,
                    run_id=outcome.run_id or "",
                    marked_by=IMPORTER,
                )
                report.causes += 1
            except TriageError:
                continue
        progress(f"{label}: {len(built.results)} tests, {len(decisions)} decided failure(s)")
    return report
