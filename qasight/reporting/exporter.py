"""Machine-readable exporters: JSON summary + JUnit XML.

Both consume the NDJSON events written by the orchestrator under
``<artifacts>/<plan>/events.ndjson`` and the per-stage Allure result trees
located by :mod:`qasight.reporting.collector`.

JUnit XML is intentionally minimal — enough for Jenkins / GitLab CI native
test reporters to recognise the pass/fail split.  Allure HTML stays the
authoritative human report.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.sax.saxutils import escape

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


def write_json_summary(*, results: CollectedResults, out: Path) -> Path:
    """Aggregate per-stage Allure results into one JSON summary file."""
    summaries = _summarise(results)
    aggregate = {
        "schema_version": "1",
        "artifacts_root": str(results.artifacts_root),
        "stages": [asdict(s) for s in summaries],
        "total": _sum(summaries, "total"),
        "passed": _sum(summaries, "passed"),
        "failed": _sum(summaries, "failed"),
        "broken": _sum(summaries, "broken"),
        "skipped": _sum(summaries, "skipped"),
    }
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(aggregate, indent=2, sort_keys=True), encoding="utf-8")
    return out


def write_junit_xml(*, results: CollectedResults, out: Path) -> Path:
    """Emit a JUnit XML file aggregated from per-stage Allure JSON files."""
    summaries = _summarise(results)
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    suites: list[str] = []
    for s in summaries:
        name = escape(f"{s.plan}.{s.stage}")
        seconds = s.duration_ms / 1000.0
        suites.append(
            f'  <testsuite name="{name}" tests="{s.total}" '
            f'failures="{s.failed}" errors="{s.broken}" skipped="{s.skipped}" '
            f'time="{seconds:.3f}" />'
        )
    body = "\n".join(suites) if suites else ""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuites name="qasight" tests="{_sum(summaries, "total")}" '
        f'failures="{_sum(summaries, "failed")}" errors="{_sum(summaries, "broken")}" '
        f'skipped="{_sum(summaries, "skipped")}">\n'
        f"{body}\n"
        "</testsuites>\n"
    )
    out.write_text(xml, encoding="utf-8")
    return out


def _summarise(results: CollectedResults) -> list[StageSummary]:
    out: list[StageSummary] = []
    for stage in results.stages:
        total = passed = failed = broken = skipped = 0
        duration_ms = 0
        for result_json in stage.results_dir.glob("*-result.json"):
            try:
                data = json.loads(result_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            total += 1
            status = str(data.get("status", "unknown")).lower()
            if status == "passed":
                passed += 1
            elif status == "failed":
                failed += 1
            elif status == "broken":
                broken += 1
            elif status == "skipped":
                skipped += 1
            duration_ms += int(_extract_duration_ms(data))
        out.append(
            StageSummary(
                plan=stage.plan,
                stage=stage.stage,
                framework=stage.framework,
                total=total,
                passed=passed,
                failed=failed,
                broken=broken,
                skipped=skipped,
                duration_ms=duration_ms,
            )
        )
    return out


def _extract_duration_ms(data: dict[str, object]) -> int:
    """Allure result JSON stores ``start``/``stop`` epoch ms timestamps."""
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


def _sum(items: list[StageSummary], field: str) -> int:
    return sum(int(getattr(s, field)) for s in items)


# Keep the regex around for potential reuse by tests that inspect the XML.
_SUITE_OPEN = re.compile(r"<testsuite ")
