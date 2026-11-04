"""Tests for archived run diff (per-test Allure case map)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from qasight.repository.models import ReportArchive
from qasight.services.report_archive import build_cycle_zip_bytes
from qasight.services.report_archive_diff import (
    align_case_keys,
    case_identity,
    diff_archives,
    diff_case_maps,
    parse_archive_uuid,
)


def _plan_with_results(root: Path, plan: str, result_payloads: list[dict]) -> bytes:
    plan_dir = root / plan
    plan_dir.mkdir(parents=True)
    (plan_dir / "events.ndjson").write_text("{}\n", encoding="utf-8")
    (plan_dir / "plan_result.json").write_text(
        json.dumps({"plan": plan, "exit_code": 0, "duration_s": 1.0}),
        encoding="utf-8",
    )
    res = plan_dir / "st" / "allure-results" / "pytest"
    res.mkdir(parents=True)
    for index, payload in enumerate(result_payloads):
        (res / f"t-{index}-result.json").write_text(json.dumps(payload), encoding="utf-8")
    blob, _summary, _ = build_cycle_zip_bytes(root, plan)
    return blob


def _plan_with_result(root: Path, plan: str, result_payload: dict) -> bytes:
    return _plan_with_results(root, plan, [result_payload])


def test_diff_archives_regression_and_fix(tmp_path: Path) -> None:
    plan = "same-cycle"
    base_blob = _plan_with_result(
        tmp_path / "b",
        plan,
        {"historyId": "hid1", "status": "passed", "name": "t", "start": 0, "stop": 1000},
    )
    cur_blob = _plan_with_result(
        tmp_path / "c",
        plan,
        {"historyId": "hid1", "status": "failed", "name": "t", "start": 0, "stop": 500},
    )

    bid, cid = uuid.uuid4(), uuid.uuid4()
    baseline = ReportArchive(
        id=bid,
        cycle_name=plan,
        exit_code=0,
        summary_json={},
        artifact_bytes=base_blob,
        total_tests=1,
        passed=1,
        failed=0,
    )
    current = ReportArchive(
        id=cid,
        cycle_name=plan,
        exit_code=1,
        summary_json={},
        artifact_bytes=cur_blob,
        total_tests=1,
        passed=0,
        failed=1,
    )

    td = tmp_path / "diffwork"
    td.mkdir()
    changes, metrics = diff_archives(baseline=baseline, current=current, tmp=td)
    kinds = {c.kind for c in changes}
    assert "regression" in kinds
    reg = next(c for c in changes if c.kind == "regression")
    assert reg.group == "(ungrouped)"
    assert metrics["baseline_id"] == str(bid)
    assert metrics["current_id"] == str(cid)

    fix_blob = _plan_with_result(
        tmp_path / "d",
        plan,
        {"historyId": "hid1", "status": "passed", "name": "t", "start": 0, "stop": 100},
    )
    fixed = ReportArchive(
        id=uuid.uuid4(),
        cycle_name=plan,
        exit_code=0,
        summary_json={},
        artifact_bytes=fix_blob,
    )
    td2 = tmp_path / "diffwork2"
    td2.mkdir()
    changes2, _ = diff_archives(baseline=current, current=fixed, tmp=td2)
    assert any(c.kind == "fix" for c in changes2)


def test_parse_archive_uuid() -> None:
    u = uuid.uuid4()
    assert parse_archive_uuid(str(u)) == u
    assert parse_archive_uuid("not-a-uuid") is None


def test_diff_archives_group_from_package_label(tmp_path: Path) -> None:
    plan = "pkg-cycle"
    labels = [{"name": "package", "value": "com.acme.tests"}]
    base_blob = _plan_with_result(
        tmp_path / "g1",
        plan,
        {
            "historyId": "hid2",
            "status": "passed",
            "name": "t2",
            "labels": labels,
            "start": 0,
            "stop": 100,
        },
    )
    cur_blob = _plan_with_result(
        tmp_path / "g2",
        plan,
        {
            "historyId": "hid2",
            "status": "failed",
            "name": "t2",
            "labels": labels,
            "start": 0,
            "stop": 200,
        },
    )
    baseline = ReportArchive(
        id=uuid.uuid4(),
        cycle_name=plan,
        exit_code=0,
        summary_json={},
        artifact_bytes=base_blob,
    )
    current = ReportArchive(
        id=uuid.uuid4(),
        cycle_name=plan,
        exit_code=1,
        summary_json={},
        artifact_bytes=cur_blob,
    )
    td = tmp_path / "diffpkg"
    td.mkdir()
    changes, _ = diff_archives(baseline=baseline, current=current, tmp=td)
    reg = next(c for c in changes if c.kind == "regression")
    assert reg.group == "com.acme.tests"


def test_diff_archives_classifies_added_removed_and_status_changes(tmp_path: Path) -> None:
    plan = "multi-case-cycle"
    base_blob = _plan_with_results(
        tmp_path / "multi-base",
        plan,
        [
            {"historyId": "removed", "status": "passed", "name": "removed case"},
            {
                "fullName": "tests.api.test_status",
                "status": "skipped",
                "name": "status case",
                "labels": [
                    {"name": "parentSuite", "value": "api"},
                    {"name": "suite", "value": "flow"},
                ],
                "start": 100,
                "stop": 150,
            },
            {"historyId": "stable", "status": "passed", "name": "stable case"},
        ],
    )
    cur_blob = _plan_with_results(
        tmp_path / "multi-current",
        plan,
        [
            {
                "fullName": "tests.api.test_status",
                "status": "broken",
                "name": "status case",
                "labels": [
                    {"name": "parentSuite", "value": "api"},
                    {"name": "suite", "value": "flow"},
                ],
                "start": 100,
                "stop": 260,
            },
            {"historyId": "added", "status": "passed", "name": "added case"},
            {"historyId": "stable", "status": "passed", "name": "stable case"},
        ],
    )
    baseline = ReportArchive(
        id=uuid.uuid4(),
        cycle_name=plan,
        exit_code=0,
        summary_json={},
        artifact_bytes=base_blob,
    )
    current = ReportArchive(
        id=uuid.uuid4(),
        cycle_name=plan,
        exit_code=1,
        summary_json={},
        artifact_bytes=cur_blob,
    )
    td = tmp_path / "diffmulti"
    td.mkdir()

    changes, _ = diff_archives(baseline=baseline, current=current, tmp=td)

    by_kind = {change.kind: change for change in changes}
    assert set(by_kind) == {"added", "removed", "status_change"}
    assert by_kind["added"].baseline_status is None
    assert by_kind["added"].current_status == "passed"
    assert by_kind["removed"].baseline_status == "passed"
    assert by_kind["removed"].current_status is None
    assert by_kind["status_change"].baseline_status == "skipped"
    assert by_kind["status_change"].current_status == "broken"
    assert by_kind["status_change"].group == "api \u203a flow"
    assert by_kind["status_change"].duration_delta_ms == 110


def _case(full_name: str, status: str = "passed", name: str | None = None) -> dict:
    return {"name": name or full_name.rsplit(".", 1)[-1], "full_name": full_name, "status": status}


def test_same_test_recorded_two_ways_is_not_added_and_removed() -> None:
    """`qasight run` (Allure historyId) then `qasight ingest` of pytest's JUnit XML (hash of tool + name)."""
    base = {"49250cb3": _case("tests.test_a#test_a")}
    cur = {"sha-of-junit": _case("tests.test_a.test_a", "failed")}

    changes = diff_case_maps(base, cur)

    assert [(c.kind, c.baseline_status, c.current_status) for c in changes] == [
        ("regression", "passed", "failed")
    ]


def test_alignment_folds_runner_separators() -> None:
    assert case_identity({"full_name": "suite > adds numbers"}) == "suite.adds numbers"
    assert case_identity({"full_name": "pkg/TestAdd"}) == "pkg.TestAdd"
    assert case_identity({"full_name": "tests/test_a.py::test_a"}) == "tests.test_a.py.test_a"


def test_ambiguous_names_stay_unmatched() -> None:
    """Two parametrized cases share a name without their ids: don't guess which is which."""
    base = {"k1": _case("t#test_p"), "k2": _case("t#test_p")}
    cur = {"j1": _case("t.test_p")}

    kinds = sorted(c.kind for c in diff_case_maps(base, cur))

    assert kinds == ["added", "removed", "removed"]


def test_matching_keys_are_left_alone() -> None:
    base = {"k": _case("a.b"), "gone": _case("old.test")}
    cur = {"k": _case("a.b"), "new": _case("new.test")}

    assert align_case_keys(base, cur) is base
