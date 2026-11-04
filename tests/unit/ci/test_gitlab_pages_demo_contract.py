"""The demo pipeline and the static UI build share a layout; keep them in step.

``scripts/export_static_site.py`` writes JSON at paths that
``frontend/src/lib/static-backend.ts`` reads back. Nothing at runtime couples
them, so these tests pin the few names both sides agree on, plus the pipeline
wiring that makes the published site work (Pages base, SPA fallback, cycles).
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE = REPO_ROOT / ".gitlab-ci.yml"
EXPORTER = REPO_ROOT / "scripts" / "export_static_site.py"
STATIC_BACKEND = REPO_ROOT / "frontend" / "src" / "lib" / "static-backend.ts"


def _pipeline() -> dict:
    return yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))


def test_pipeline_runs_cycles_then_publishes_pages() -> None:
    payload = _pipeline()
    assert payload["stages"] == ["demo", "deploy"]

    demo = payload["run_demo_cycles"]
    script = "\n".join(demo["script"])
    assert 'git clone --depth 1 --branch "$FAKE_API_REF" "$FAKE_API_REPO" .demo/fake-api' in script
    assert "qasight run --cycle self-test --ci" in script
    assert "qasight run --cycle fake-api --ci" in script
    assert "scripts/export_static_site.py" in script
    assert "public" in demo["artifacts"]["paths"]

    pages = payload["pages"]
    assert pages["needs"][0]["job"] == "run_demo_cycles"
    assert pages["artifacts"]["paths"] == ["public"]


def test_only_the_fake_app_is_allowed_to_fail() -> None:
    script = _pipeline()["run_demo_cycles"]["script"]
    self_test = next(line for line in script if "--cycle self-test" in line)
    assert "pipefail" in self_test and "|| true" not in self_test
    for line in script:
        if "--cycle fake-api" in line:
            assert line.endswith("|| true")


def test_demo_cycles_exist_in_the_config() -> None:
    config = yaml.safe_load((REPO_ROOT / "qasight.yaml").read_text(encoding="utf-8"))
    assert "self-test" in config["cycles"]
    fake = config["cycles"]["fake-api"]
    # The pipeline clones the target here; the cycle has to look in the same place.
    assert {stage["target_repo"] for stage in fake["stages"]} == {".demo/fake-api"}
    assert {stage["tier"] for stage in fake["stages"]} == {"unit", "integration", "e2e"}


def test_pages_job_wires_the_static_build_and_spa_fallback() -> None:
    script = "\n".join(_pipeline()["pages"]["script"])
    # Vite needs the Pages subpath; the shim needs the data directory under it.
    assert "VITE_BASE=" in script
    assert "VITE_STATIC_DATA_BASE=" in script
    assert "VITE_API_BASE_URL=" in script
    # GitLab Pages serves 404.html for unknown paths, which is how deep links survive a refresh.
    assert "cp public/index.html public/404.html" in script


def test_exporter_and_frontend_shim_agree_on_the_file_layout() -> None:
    exporter = EXPORTER.read_text(encoding="utf-8")
    shim = STATIC_BACKEND.read_text(encoding="utf-8")
    for name in (
        "manifest.json",
        "health.json",
        "ai-config.json",
        "cycles.json",
        "runs.json",
        "dashboard/overview.json",
        "dashboard/recent-runs.json",
        "comparison.json",
        "cases.json",
        "detail.json",
    ):
        assert name in exporter, f"exporter no longer writes {name}"
        assert name in shim, f"the static backend no longer reads {name}"

    # The per-run extras are one file per endpoint name on both sides.
    for name in ("reports", "pyramid", "ai-summary"):
        assert f"{name}.json" in exporter, f"exporter no longer writes {name}.json"
        assert name in shim, f"the static backend no longer maps /{name}"


GITHUB_PAGES_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pages-demo.yml"


def _github_pages_workflow() -> dict:
    return yaml.safe_load(GITHUB_PAGES_WORKFLOW.read_text(encoding="utf-8"))


def test_github_pages_workflow_runs_the_same_cycles_and_build() -> None:
    build = _github_pages_workflow()["jobs"]["build"]
    steps = {step.get("name"): step for step in build["steps"] if step.get("name")}
    script = "\n".join(str(step.get("run", "")) for step in build["steps"])

    assert 'git clone --depth 1 --branch "$FAKE_API_REF" "$FAKE_API_REPO" .demo/fake-api' in script
    self_test = steps["QASight tests itself"]["run"]
    assert "pipefail" in self_test and "|| true" not in self_test
    assert steps["QASight tests fake-api (fails on purpose)"]["run"].endswith("|| true")
    assert "scripts/export_static_site.py" in script

    ui_env = steps["Build the UI in static mode"]["env"]
    assert {"VITE_BASE", "VITE_STATIC_DATA_BASE", "VITE_API_BASE_URL"} <= set(ui_env)
    assert "cp public/index.html public/404.html" in script


def test_github_pages_workflow_deploys_only_outside_pull_requests() -> None:
    workflow = _github_pages_workflow()
    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert deploy["if"] == "github.event_name != 'pull_request'"
    assert deploy["environment"]["name"] == "github-pages"
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    # Workflow-wide token stays read-only; only the deploy job may write Pages.
    assert workflow["permissions"] == {"contents": "read"}
