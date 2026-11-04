"""Convert a k6 end-of-test summary (JSON) into Allure ``*-result.json`` dicts.

A load test has no test cases; what passes or fails in k6 is its **checks**
(``check(res, {"status is 200": ...})``) and its **thresholds**
(``http_req_duration: ["p(95)<500"]``). Each becomes one result, so a check
that started failing or a threshold that got crossed shows up in history,
Compare and flaky detection like a failing test.

Two files carry them, and both are read:

- ``k6 run --summary-export=k6-summary.json script.js`` (checks and groups keyed
  by name; a threshold's value is ``true`` when it **failed**);
- the ``data`` object a script's ``handleSummary`` writes with
  ``JSON.stringify(data)`` (checks and groups as lists; a threshold is
  ``{"ok": false}`` when it failed).

Status mapping:

    check with no fails        -> passed
    check with any fails       -> failed  ("3 of 120 failed")
    threshold met              -> passed
    threshold crossed          -> failed  (with the metric's values)

``full_name`` is ``checks › group › check`` or ``thresholds › metric › expression``.
Results carry no duration: each would span the whole run, and summing them
would multiply its length.
"""

from __future__ import annotations

from typing import Any

from qasight.reporting.imported_results import make_result

_METRIC_VALUES = ("avg", "min", "med", "max", "p(90)", "p(95)", "p(99)", "rate", "count")


def is_k6_summary(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("metrics"), dict)
        and isinstance(data.get("root_group"), dict)
    )


def _children(value: Any) -> list[dict[str, Any]]:
    """A ``groups`` or ``checks`` entry as a list: the export keys them by name, handleSummary lists them."""
    if isinstance(value, dict):
        value = list(value.values())
    return [item for item in value or [] if isinstance(item, dict)]


def _checks(group: dict[str, Any], path: tuple[str, ...]) -> list[tuple[tuple[str, ...], dict]]:
    found = [(path, check) for check in _children(group.get("checks"))]
    for sub in _children(group.get("groups")):
        found += _checks(sub, (*path, str(sub.get("name") or "")))
    return found


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _threshold_failed(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("ok") is False
    return value is True


def _metric_values(metric: dict[str, Any]) -> str:
    values: dict[str, Any] = metric["values"] if isinstance(metric.get("values"), dict) else metric
    shown = [
        f"{key}={values[key]:g}"
        for key in _METRIC_VALUES
        if isinstance(values.get(key), (int, float))
    ]
    return ", ".join(shown)


def k6_results_for_summary(
    data: dict[str, Any], *, tool: str, fallback_ms: int
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for path, check in _checks(data["root_group"], ()):
        name = str(check.get("name") or "(unnamed check)")
        passes, fails = _count(check.get("passes")), _count(check.get("fails"))
        group = " › ".join(p for p in path if p)
        failed = fails > 0
        results.append(
            make_result(
                tool=tool,
                framework="k6",
                language="javascript",
                name=name,
                full_name=" › ".join(p for p in ("checks", group, name) if p),
                status="failed" if failed else "passed",
                start_ms=fallback_ms,
                duration_ms=0,
                suite="checks",
                message=f"{fails} of {passes + fails} failed" if failed else None,
                labels=[{"name": "subSuite", "value": group}] if group else [],
            )
        )

    for metric_name, metric in sorted(data["metrics"].items()):
        if not isinstance(metric, dict) or not isinstance(metric.get("thresholds"), dict):
            continue
        for expression, value in metric["thresholds"].items():
            failed = _threshold_failed(value)
            values = _metric_values(metric)
            results.append(
                make_result(
                    tool=tool,
                    framework="k6",
                    language="javascript",
                    name=f"{metric_name} {expression}",
                    full_name=f"thresholds › {metric_name} › {expression}",
                    status="failed" if failed else "passed",
                    start_ms=fallback_ms,
                    duration_ms=0,
                    suite="thresholds",
                    message=(
                        f"threshold crossed: {metric_name} {expression}"
                        + (f" ({values})" if values else "")
                    )
                    if failed
                    else None,
                )
            )
    return results
