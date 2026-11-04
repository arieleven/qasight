"""ReportPortal REST API client with v1/v2 auto-detection."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import requests

from qasight.reporting.allure_results import (
    RunAggregate,
    TestCaseRecord,
    map_status_to_reportportal,
)

logger = logging.getLogger(__name__)


class ReportPortalError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ReportPortalClient:
    """Thin REST client for launch + item reporting."""

    def __init__(
        self,
        endpoint: str,
        project: str,
        token: str,
        *,
        api_version: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.endpoint = endpoint.strip().rstrip("/")
        self.project = project.strip()
        self.token = token.strip()
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
        )
        if api_version and api_version.lower() in ("v1", "v2"):
            self.api_version = api_version.lower()
        else:
            self.api_version = self._detect_api_version()

    @property
    def _api_base(self) -> str:
        return f"{self.endpoint}/api/{self.api_version}/{self.project}"

    def validate(self) -> None:
        """Probe API reachability and credentials."""
        url = f"{self._api_base}/launch"
        resp = self._session.get(url, params={"page.size": 1}, timeout=self.timeout_s)
        if resp.status_code == 401:
            raise ReportPortalError("ReportPortal authentication failed (401)", status_code=401)
        if resp.status_code == 404:
            raise ReportPortalError(
                f"ReportPortal project {self.project!r} not found at {self.endpoint}",
                status_code=404,
            )
        if resp.status_code >= 500:
            raise ReportPortalError(
                f"ReportPortal server error: HTTP {resp.status_code}",
                status_code=resp.status_code,
            )

    def start_launch(
        self,
        name: str,
        *,
        attributes: list[dict[str, str]] | None = None,
        description: str = "",
        start_time_ms: int | None = None,
    ) -> str:
        launch_uuid = str(uuid.uuid4())
        body: dict[str, Any] = {
            "name": name,
            "startTime": str(start_time_ms or _now_ms()),
            "uuid": launch_uuid,
            "attributes": attributes or [],
            "mode": "DEFAULT",
        }
        if description:
            body["description"] = description
        data = self._post(f"{self._api_base}/launch", body)
        return str(data.get("id") or launch_uuid)

    def start_item(
        self,
        name: str,
        *,
        item_type: str,
        launch_uuid: str,
        parent_uuid: str | None = None,
        start_time_ms: int | None = None,
        description: str = "",
    ) -> str:
        item_uuid = str(uuid.uuid4())
        body: dict[str, Any] = {
            "name": name,
            "startTime": str(start_time_ms or _now_ms()),
            "type": item_type,
            "launchUuid": launch_uuid,
            "uuid": item_uuid,
        }
        if description:
            body["description"] = description
        if parent_uuid:
            url = f"{self._api_base}/item/{parent_uuid}"
        else:
            url = f"{self._api_base}/item"
        data = self._post(url, body)
        return str(data.get("id") or item_uuid)

    def finish_item(
        self,
        item_uuid: str,
        *,
        launch_uuid: str,
        end_time_ms: int | None = None,
        status: str | None = None,
        issue_comment: str | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "endTime": str(end_time_ms or _now_ms()),
            "launchUuid": launch_uuid,
        }
        if status:
            body["status"] = status
        if issue_comment:
            body["issue"] = {"issueType": "to_investigate", "comment": issue_comment[:500]}
        self._put(f"{self._api_base}/item/{item_uuid}", body)

    def finish_launch(
        self,
        launch_uuid: str,
        *,
        end_time_ms: int | None = None,
        status: str = "passed",
    ) -> None:
        body = {
            "endTime": str(end_time_ms or _now_ms()),
            "status": status,
        }
        self._put(f"{self._api_base}/launch/{launch_uuid}/finish", body)

    def save_log(
        self,
        *,
        launch_uuid: str,
        item_uuid: str,
        message: str,
        level: str = "ERROR",
        time_ms: int | None = None,
    ) -> None:
        body = {
            "launchUuid": launch_uuid,
            "itemUuid": item_uuid,
            "time": str(time_ms or _now_ms()),
            "message": message[:2000],
            "level": level,
        }
        self._post(f"{self._api_base}/log", body)

    def report_aggregate(
        self,
        aggregate: RunAggregate,
        *,
        launch_name: str,
        attributes: list[dict[str, str]] | None = None,
    ) -> str:
        """Stream full suite tree and return launch UUID."""
        launch_start_ms, launch_end_ms = _resolve_launch_window(aggregate)
        launch_uuid = self.start_launch(
            launch_name,
            attributes=attributes,
            start_time_ms=launch_start_ms,
        )
        try:
            for stage in aggregate.stages:
                stage_tests = [
                    t for t in aggregate.tests if t.plan == stage.plan and t.stage == stage.stage
                ]
                suite_start_ms, suite_end_ms = _resolve_stage_window(
                    stage_tests,
                    launch_start_ms=launch_start_ms,
                    launch_end_ms=launch_end_ms,
                )
                suite_uuid = self.start_item(
                    f"{stage.plan}.{stage.stage}",
                    item_type="suite",
                    launch_uuid=launch_uuid,
                    start_time_ms=suite_start_ms,
                    description=f"framework={stage.framework}",
                )
                for test in stage_tests:
                    self._report_test(
                        test,
                        launch_uuid=launch_uuid,
                        parent_uuid=suite_uuid,
                        launch_start_ms=launch_start_ms,
                        launch_end_ms=launch_end_ms,
                    )
                suite_status = "passed" if stage.failed == 0 and stage.broken == 0 else "failed"
                self.finish_item(
                    suite_uuid,
                    launch_uuid=launch_uuid,
                    end_time_ms=suite_end_ms,
                    status=suite_status,
                )
            launch_status = "passed" if aggregate.overall_passed else "failed"
            self.finish_launch(launch_uuid, end_time_ms=launch_end_ms, status=launch_status)
        except Exception:
            # Broad on purpose: close the launch on any failure, then re-raise unchanged.
            try:
                self.finish_launch(launch_uuid, end_time_ms=launch_end_ms, status="failed")
            except (ReportPortalError, requests.RequestException):
                logger.warning("could not close ReportPortal launch %s", launch_uuid, exc_info=True)
            raise
        return launch_uuid

    def _report_test(
        self,
        test: TestCaseRecord,
        *,
        launch_uuid: str,
        parent_uuid: str,
        launch_start_ms: int,
        launch_end_ms: int,
    ) -> None:
        start_ms, stop_ms = _normalize_test_times(
            test,
            launch_start_ms=launch_start_ms,
            launch_end_ms=launch_end_ms,
        )
        item_uuid = self.start_item(
            test.name,
            item_type="test",
            launch_uuid=launch_uuid,
            parent_uuid=parent_uuid,
            start_time_ms=start_ms,
            description=test.full_name,
        )
        rp_status = map_status_to_reportportal(test.status)
        self.finish_item(
            item_uuid,
            launch_uuid=launch_uuid,
            end_time_ms=stop_ms,
            status=rp_status,
            issue_comment=test.failure_message if test.status in ("failed", "broken") else None,
        )
        if test.failure_message and test.status in ("failed", "broken"):
            try:
                self.save_log(
                    launch_uuid=launch_uuid,
                    item_uuid=item_uuid,
                    message=test.failure_message,
                )
            except (ReportPortalError, requests.RequestException):
                # The item is already reported; a missing log line is not worth failing the upload.
                logger.warning("could not attach failure log to %s", test.name, exc_info=True)

    def dashboard_url(self, launch_uuid: str) -> str:
        return f"{self.endpoint}/ui/#{self.project}/launches/all/{launch_uuid}"

    def _detect_api_version(self) -> str:
        for version in ("v2", "v1"):
            url = f"{self.endpoint}/api/{version}/{self.project}/launch"
            try:
                resp = self._session.get(url, params={"page.size": 1}, timeout=self.timeout_s)
                if resp.status_code in (200, 401, 403):
                    return version
            except requests.RequestException:
                continue
        return "v1"

    def _post(self, url: str, body: dict) -> dict:
        resp = self._session.post(url, json=body, timeout=self.timeout_s)
        return self._parse_response(resp)

    def _put(self, url: str, body: dict) -> dict:
        resp = self._session.put(url, json=body, timeout=self.timeout_s)
        return self._parse_response(resp)

    def _parse_response(self, resp: requests.Response) -> dict:
        if resp.status_code == 401:
            raise ReportPortalError("ReportPortal authentication failed", status_code=401)
        if resp.status_code >= 400:
            snippet = (resp.text or "")[:300]
            raise ReportPortalError(
                f"ReportPortal API error HTTP {resp.status_code}: {snippet}",
                status_code=resp.status_code,
            )
        if not resp.text.strip():
            return {}
        try:
            data = resp.json()
        except ValueError as exc:
            raise ReportPortalError(f"Invalid JSON from ReportPortal: {exc}") from exc
        return data if isinstance(data, dict) else {}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve_launch_window(aggregate: RunAggregate) -> tuple[int, int]:
    """Align launch bounds with Allure timestamps so children are not before the parent."""
    now = _now_ms()
    starts = [t.start_ms for t in aggregate.tests if t.start_ms is not None]
    stops = [t.stop_ms for t in aggregate.tests if t.stop_ms is not None]
    launch_start = min(starts) if starts else now
    if stops:
        launch_end = max(max(stops), launch_start)
    elif aggregate.duration_ms > 0:
        launch_end = launch_start + aggregate.duration_ms
    else:
        launch_end = max(now, launch_start)
    return launch_start, launch_end


def _resolve_stage_window(
    stage_tests: list[TestCaseRecord],
    *,
    launch_start_ms: int,
    launch_end_ms: int,
) -> tuple[int, int]:
    if not stage_tests:
        return launch_start_ms, launch_end_ms
    starts: list[int] = []
    stops: list[int] = []
    for test in stage_tests:
        start_ms, stop_ms = _normalize_test_times(
            test,
            launch_start_ms=launch_start_ms,
            launch_end_ms=launch_end_ms,
        )
        starts.append(start_ms)
        stops.append(stop_ms)
    return min(starts), max(stops)


def _normalize_test_times(
    test: TestCaseRecord,
    *,
    launch_start_ms: int,
    launch_end_ms: int,
) -> tuple[int, int]:
    start_ms = test.start_ms if test.start_ms is not None else launch_start_ms
    if test.stop_ms is not None:
        stop_ms = test.stop_ms
    elif test.duration_ms > 0:
        stop_ms = start_ms + test.duration_ms
    else:
        stop_ms = launch_end_ms
    start_ms = max(start_ms, launch_start_ms)
    stop_ms = max(stop_ms, start_ms)
    return start_ms, stop_ms
