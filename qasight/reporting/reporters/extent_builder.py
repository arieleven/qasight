"""Build and render the Extent-style HTML dashboard from Allure results."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from qasight.reporting.allure_results import RunAggregate, format_duration
from qasight.reporting.reporters.base import ReportContext

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def render_dashboard(
    aggregate: RunAggregate,
    *,
    context: ReportContext,
    output_dir: Path,
) -> Path:
    """Render ``index.html`` under ``output_dir`` and return its path."""
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("extent_dashboard.html.j2")

    run_title = context.plan_name or context.run_id or "testo-run"
    stage_groups = _group_tests_by_stage(aggregate)
    chart = _chart_segments(aggregate)

    html = template.render(
        run_title=run_title,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        overall_status="PASS" if aggregate.overall_passed else "FAIL",
        total=aggregate.total,
        passed=aggregate.passed,
        failed=aggregate.failed,
        broken=aggregate.broken,
        skipped=aggregate.skipped,
        duration_label=format_duration(aggregate.duration_ms),
        chart=chart,
        stage_groups=stage_groups,
    )

    index_path = output_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")
    return index_path


def _group_tests_by_stage(aggregate: RunAggregate) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for test in aggregate.tests:
        key = f"{test.plan} / {test.stage} ({test.framework})"
        grouped[key].append(
            {
                "id": test.id,
                "name": test.name,
                "full_name": test.full_name,
                "status": test.status,
                "duration_ms": test.duration_ms,
                "duration_label": format_duration(test.duration_ms),
                "failure_message": test.failure_message,
                "description": test.description,
            }
        )
    out: list[dict] = []
    for stage_name, tests in sorted(grouped.items()):
        failed = sum(1 for t in tests if t["status"] in ("failed", "broken"))
        out.append(
            {
                "name": stage_name,
                "total": len(tests),
                "failed": failed,
                "tests": sorted(tests, key=lambda t: t["name"].lower()),
            }
        )
    return out


def _chart_segments(aggregate: RunAggregate) -> dict:
    total = max(aggregate.total, 1)
    return {
        "passed_pct": round(100 * aggregate.passed / total, 1),
        "failed_pct": round(100 * aggregate.failed / total, 1),
        "broken_pct": round(100 * aggregate.broken / total, 1),
        "skipped_pct": round(100 * aggregate.skipped / total, 1),
        "passed": aggregate.passed,
        "failed": aggregate.failed,
        "broken": aggregate.broken,
        "skipped": aggregate.skipped,
        "json": json.dumps(
            {
                "passed": aggregate.passed,
                "failed": aggregate.failed,
                "broken": aggregate.broken,
                "skipped": aggregate.skipped,
            }
        ),
    }
