"""Parse Allure ``*-result.json`` files from :class:`CollectedResults`."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from qasight.reporting.collector import CollectedResults


@dataclass(frozen=True)
class StageSummary:
    plan: str
    stage: str
    framework: str
    total: int
    passed: int
    failed: int
    broken: int
    skipped: int
    duration_ms: int

    @property
    def status(self) -> str:
        if self.failed or self.broken:
            return "failed"
        if self.total == 0:
            return "empty"
        return "passed"


@dataclass(frozen=True)
class TestCaseRecord:
    # Not a pytest test class, although its name starts with "Test".
    __test__ = False

    id: str
    name: str
    full_name: str
    status: str
    duration_ms: int
    plan: str
    stage: str
    framework: str
    description: str | None
    failure_message: str | None
    start_ms: int | None
    stop_ms: int | None


@dataclass(frozen=True)
class RunAggregate:
    artifacts_root: str
    total: int
    passed: int
    failed: int
    broken: int
    skipped: int
    duration_ms: int
    stages: tuple[StageSummary, ...]
    tests: tuple[TestCaseRecord, ...]

    @property
    def overall_passed(self) -> bool:
        return self.failed == 0 and self.broken == 0


def parse_collected_results(results: CollectedResults) -> RunAggregate:
    """Walk every stage tree and build per-test records plus stage summaries."""
    stages: list[StageSummary] = []
    tests: list[TestCaseRecord] = []

    for stage in results.stages:
        total = passed = failed = broken = skipped = 0
        stage_duration_ms = 0

        for result_json in sorted(stage.results_dir.glob("*-result.json")):
            try:
                data = json.loads(result_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            status = str(data.get("status", "unknown")).lower()
            duration_ms = _extract_duration_ms(data)
            start_ms = _to_int_or_none(data.get("start"))
            stop_ms = _to_int_or_none(data.get("stop"))

            total += 1
            if status == "passed":
                passed += 1
            elif status == "failed":
                failed += 1
            elif status == "broken":
                broken += 1
            elif status == "skipped":
                skipped += 1

            stage_duration_ms += duration_ms
            name = str(data.get("name") or result_json.stem)
            full_name = str(data.get("fullName") or name)
            tests.append(
                TestCaseRecord(
                    id=_stable_test_id(
                        plan=stage.plan,
                        stage=stage.stage,
                        full_name=full_name,
                        uuid=data.get("uuid"),
                    ),
                    name=name,
                    full_name=full_name,
                    status=status,
                    duration_ms=duration_ms,
                    plan=stage.plan,
                    stage=stage.stage,
                    framework=stage.framework,
                    description=_optional_str(data.get("description")),
                    failure_message=_failure_message(data),
                    start_ms=start_ms,
                    stop_ms=stop_ms,
                )
            )

        stages.append(
            StageSummary(
                plan=stage.plan,
                stage=stage.stage,
                framework=stage.framework,
                total=total,
                passed=passed,
                failed=failed,
                broken=broken,
                skipped=skipped,
                duration_ms=stage_duration_ms,
            )
        )

    duration_ms = _run_duration_ms(tests)
    return RunAggregate(
        artifacts_root=str(results.artifacts_root),
        total=sum(s.total for s in stages),
        passed=sum(s.passed for s in stages),
        failed=sum(s.failed for s in stages),
        broken=sum(s.broken for s in stages),
        skipped=sum(s.skipped for s in stages),
        duration_ms=duration_ms,
        stages=tuple(stages),
        tests=tuple(tests),
    )


def map_status_to_reportportal(status: str) -> str:
    """Map Allure status strings to ReportPortal finish statuses (lowercase)."""
    normalised = status.lower()
    mapping = {
        "passed": "passed",
        "failed": "failed",
        "broken": "failed",
        "skipped": "skipped",
    }
    return mapping.get(normalised, "failed")


def format_duration(duration_ms: int) -> str:
    """Human-readable duration (e.g. ``2m 15s``)."""
    if duration_ms <= 0:
        return "0s"
    seconds = duration_ms // 1000
    minutes, secs = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _summarise_stages(results: CollectedResults) -> list[StageSummary]:
    return list(parse_collected_results(results).stages)


def _stable_test_id(*, plan: str, stage: str, full_name: str, uuid: object) -> str:
    if uuid is not None and str(uuid).strip():
        return str(uuid).strip()
    key = f"{plan}:{stage}:{full_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _failure_message(data: dict) -> str | None:
    details = data.get("statusDetails")
    if isinstance(details, dict):
        msg = details.get("message") or details.get("trace")
        if msg:
            return str(msg)
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_duration_ms(data: dict) -> int:
    start = _to_int(data.get("start"))
    stop = _to_int(data.get("stop"))
    if start and stop and stop >= start:
        return int(stop - start)
    return 0


def _to_int(value: object) -> int:
    if not isinstance(value, (int, float, str)):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _to_int_or_none(value: object) -> int | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _run_duration_ms(tests: list[TestCaseRecord]) -> int:
    starts = [t.start_ms for t in tests if t.start_ms is not None]
    stops = [t.stop_ms for t in tests if t.stop_ms is not None]
    if starts and stops:
        span = max(stops) - min(starts)
        if span > 0:
            return int(span)
    return sum(t.duration_ms for t in tests)
