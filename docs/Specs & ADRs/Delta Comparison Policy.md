---
type: spec
status: current
created: 2026-05-02
updated: 2026-11-04
---

# Delta Comparison Policy

This document defines deterministic semantics for Phase 3 delta analytics.

## Run role semantics

- `baseline_run_id`: reference run used as the comparison anchor.
- `current_run_id`: run being evaluated against baseline.
- Delta formula:
  - `absolute_delta = current_value - baseline_value`
  - `relative_delta_pct = ((current_value - baseline_value) / baseline_value) * 100` when baseline is non-zero.

## Classification labels

- `improvement`: metric moved in a favorable direction.
- `regression`: metric moved in an unfavorable direction.
- `neutral`: no change (`absolute_delta == 0`), or a duration change within timing noise (see below).
- `unknown`: metric cannot be compared due to missing or incompatible data.

## Direction policy table

| Metric | Group | Better Direction | Notes |
| --- | --- | --- | --- |
| `total_tests` | reliability | higher | More executed tests means broader reliability signal. |
| `passed` | reliability | higher | More passing tests is better. |
| `failed` | reliability | lower | More failed tests is worse. |
| `broken` | reliability | lower | More broken tests is worse. |
| `skipped` | reliability | lower | More skipped tests is treated as worse coverage quality. |
| `health_pct` | reliability | higher | Higher health percentage is better. |
| `wall_duration_ms` | performance | lower | Lower wall-clock duration is better. |
| `metrics_duration_ms` | performance | lower | Lower aggregate metrics duration is better. |
| `avg_case_ms` | performance | lower | Lower average case duration is better. |

## Timing noise (added 2026-10-25)

Two identical runs never take exactly the same time, so a duration metric (`unit: ms`) is only a regression or an improvement when the change is at least **5 %** of the baseline **and** at least the metric's floor: **100 ms** for `wall_duration_ms` and `metrics_duration_ms`, **5 ms** for `avg_case_ms`. A smaller change is `neutral` with reason `within_noise`; its `absolute_delta` and `relative_delta_pct` are still reported, and Compare shows it as "Within noise". The dashboard's duration trend uses the same rule (`delta_service.within_noise`, 100 ms floor) and reads "flat". Test counts and health have no noise floor: one more failed test is always a regression. The constants are `NOISE_RELATIVE_PCT` and `_MetricPolicy.noise_floor_ms` in `qasight/services/delta_service.py`.

## Unknown/null reason codes

- `missing_current_metric`: current run has no value.
- `missing_baseline_metric`: baseline run has no value.
- `zero_baseline_for_relative`: absolute delta can be computed, relative percent cannot.
- `incompatible_test_kind`: run pair has different `test_kind`.
- `within_noise`: not unknown, but the reason a `neutral` duration is neutral despite a non-zero delta.

## Stage-level deltas (added 2026-07-23)

`GET /api/v1/analytics/delta` also returns `stage_deltas`: one entry per stage name present on either run's `stage_health`, matched by `name` (no archive extraction — reuses the `stage_health` already loaded on each `CompletedRunView`). Classification uses the same `higher_is_better` rule as `health_pct` above, applied to each stage's own `health_pct`. A stage present on only one side (renamed/added/removed since the baseline ran) gets `classification: "unknown"` with the missing side's fields `null`, rather than being dropped — mirrors the metric-level `missing_*_metric` reasons in spirit, but stage deltas don't carry a `reason` field.

## Per-test deltas (added 2026-07-23)

`GET /api/v1/analytics/delta/cases` returns the full per-test breakdown — the API/UI equivalent of `qasight diff`'s non-`--metrics-only` output. Each test case is matched across the two runs by `historyId` → `fullName` → `uuid` (first present, in that order) and classified as:

- `added`: present in current, not in baseline.
- `removed`: present in baseline, not in current.
- `regression`: `passed` in baseline → `failed`/`broken` in current.
- `fix`: `failed`/`broken` in baseline → `passed` in current.
- `status_change`: any other status transition.
- (`unchanged` cases are computed but dropped from the response — only actual changes are returned.)

**Same suite, recorded two ways (added 2026-11-01).** The key comes from the source: Allure's `historyId` for `qasight run`, a hash of tool and name for `qasight ingest` and `pytest --qasight`. So one suite recorded by `qasight run` and then by `qasight ingest` would read as every test removed and added again. Before classifying, a key found on only one side is paired with a key on the other side whose name matches once `#`, `::`, `/` and ` > ` are folded to `.` (`align_case_keys()` in `qasight/services/report_archive_diff.py`), when exactly one test on each side has that name; ambiguous names (parametrized cases without their ids) stay unpaired. The pull request comment and `qasight try` also say when the stages changed since the compared run, since durations and flaky history stay per stage.

Unlike the run/stage-level deltas, this is **computed on demand from each run's live artifact snapshot every request, with no caching** — see [CLI-UI Parity - Pyramid, Graphs, Deep Diff - 2026-07-23](../Archive/CLI-UI%20Parity%20-%20Pyramid,%20Graphs,%20Deep%20Diff%20-%202026-07-23.md) for why (archives aren't large enough yet to justify the cache-invalidation work) and for why this reads from each run's snapshot directory rather than the separate `ReportArchive` table (`qasight report list`/`diff`'s id space, which has no link to run-history run ids).

