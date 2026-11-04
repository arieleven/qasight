"""``qasight export-site`` and the Action's ``static-site`` input (R3 of the new-user journey audit)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight_api.static_site import (
    STATIC_DATA_META,
    SiteError,
    build_site,
    github_pages_base_path,
    normalize_base_path,
    prepare_index_html,
)

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

# What scripts/build_ui_bundle.sh produces (VITE_BASE=./): a <base href> and relative assets.
BUNDLED_INDEX = """<!doctype html>
<html lang="en">
  <head>
    <base href="/" />
    <title>QASight</title>
    <script type="module" crossorigin src="./assets/index-abc.js"></script>
  </head>
  <body><div id="root"></div></body>
</html>
"""

JUNIT = """<testsuite name="s" tests="2" failures="1">
<testcase classname="a" name="ok" time="0.1"/>
<testcase classname="a" name="bad" time="0.2"><failure message="boom">x</failure></testcase>
</testsuite>
"""


@pytest.fixture
def history_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    url = f"sqlite:///{tmp_path / 'history.db'}"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("TESTO_HOME", str(tmp_path / "home"))
    for name in ("TESTO_SERVER_URL", "TESTO_API_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from qasight.repository.db_config import reset_engine_cache

    reset_engine_cache()
    yield url
    reset_engine_cache()


@pytest.fixture
def ui_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ui = tmp_path / "ui"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text(BUNDLED_INDEX)
    (ui / "assets" / "index-abc.js").write_text("console.log('ui')")
    monkeypatch.setenv("TESTO_UI_DIR", str(ui))
    return ui


def _record_two_runs(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(JUNIT)
    runner = CliRunner()
    for _ in range(2):
        result = runner.invoke(app, ["ingest", str(junit), "--cycle", "web"])
        assert result.exit_code == 1, result.output  # one test failed


@pytest.mark.parametrize(
    ("base_path", "site_url", "expected"),
    [
        (None, "", "/"),
        ("my-repo", "", "/my-repo/"),
        ("/my-repo/", "", "/my-repo/"),
        (None, "https://me.github.io/my-repo", "/my-repo/"),
        ("/", "https://me.github.io/my-repo/", "/"),
    ],
)
def test_base_path_comes_from_the_flag_then_the_site_url(
    base_path: str | None, site_url: str, expected: str
) -> None:
    assert normalize_base_path(base_path, site_url) == expected


def test_github_pages_path_of_a_project_and_of_a_user_site() -> None:
    assert github_pages_base_path("me/my-repo") == "/my-repo/"
    assert github_pages_base_path("Me/me.github.io") == "/"


def test_index_html_points_at_the_subpath_and_switches_to_static_mode() -> None:
    html = prepare_index_html(BUNDLED_INDEX, "/my-repo/")
    assert '<base href="/my-repo/" />' in html and '<base href="/" />' not in html
    assert html.count(STATIC_DATA_META) == 1
    # Running it twice changes nothing more.
    assert prepare_index_html(html, "/my-repo/") == html


def test_a_ui_built_for_one_fixed_path_is_refused() -> None:
    with pytest.raises(SiteError, match="base href"):
        prepare_index_html('<html><head><script src="/assets/x.js"></script></head></html>', "/")


def test_build_site_writes_the_ui_the_data_and_the_pages_fallback(
    history_db: str, ui_bundle: Path, tmp_path: Path
) -> None:
    _record_two_runs(tmp_path)
    out = tmp_path / "site"

    result = build_site(out, ui_dir=ui_bundle, base_path="/my-repo/")

    assert result.runs == 2
    index = (out / "index.html").read_text()
    assert '<base href="/my-repo/" />' in index and STATIC_DATA_META in index
    assert (out / "404.html").read_text() == index
    assert (out / ".nojekyll").exists()
    assert (out / "assets" / "index-abc.js").exists()
    manifest = json.loads((out / "data" / "manifest.json").read_text())
    assert manifest["demo"] is False and len(manifest["run_ids"]) == 2
    runs = json.loads((out / "data" / "runs.json").read_text())["items"]
    assert {run["cycle"] for run in runs} == {"web"}
    assert (out / "data" / "cycles" / "web.json").exists()
    assert len(manifest["delta_pairs"]) == 1
    # The bundle itself is untouched: `qasight ui` keeps serving it at "/".
    assert (ui_bundle / "index.html").read_text() == BUNDLED_INDEX


def test_cli_writes_the_site(history_db: str, ui_bundle: Path, tmp_path: Path) -> None:
    _record_two_runs(tmp_path)
    result = CliRunner().invoke(
        app, ["export-site", "--out", "public", "--site-url", "https://me.github.io/proj/"]
    )
    assert result.exit_code == 0, result.output
    assert "2 run(s)" in result.output and "served under /proj/" in result.output
    assert '<base href="/proj/" />' in (tmp_path / "public" / "index.html").read_text()


def test_cli_without_runs_says_how_to_record_one(history_db: str, ui_bundle: Path) -> None:
    result = CliRunner().invoke(app, ["export-site"])
    assert result.exit_code == 2
    assert "record one first" in result.output


def test_cli_without_a_built_ui_explains_where_to_get_one(
    history_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TESTO_UI_DIR", str(tmp_path / "nothing-here"))
    result = CliRunner().invoke(app, ["export-site"])
    assert result.exit_code == 2
    assert "No built UI found" in result.output


# --- the Action's static-site input -----------------------------------------


def _wrapper() -> ModuleType:
    script = Path(__file__).resolve().parents[3] / "integrations/github-action/run_testo_action.py"
    spec = importlib.util.spec_from_file_location("run_testo_action_site", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_action_rule_for_the_pages_path_matches_the_package() -> None:
    module = _wrapper()
    for repo in ("me/my-repo", "me/me.github.io", "Org/org.GitHub.io"):
        assert module.pages_base_path(repo) == github_pages_base_path(repo)


def test_action_export_installs_the_api_extra_then_writes_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _wrapper()
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module, "api_requirements", lambda: ["fastapi>=0.115.0"])
    output = tmp_path / "out.txt"
    env = {"GITHUB_REPOSITORY": "me/my-repo", "GITHUB_OUTPUT": str(output)}

    assert module.export_static_site(out=tmp_path / "site", base_path="", env=env) == 0

    assert calls[0][-1] == "fastapi>=0.115.0"
    assert calls[1] == [
        "qasight",
        "export-site",
        "--out",
        str(tmp_path / "site"),
        "--base-path",
        "/my-repo/",
    ]
    assert f"site_dir={tmp_path / 'site'}" in output.read_text()


def test_action_export_failure_is_a_warning_without_an_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _wrapper()
    monkeypatch.setattr(
        module.subprocess, "run", lambda cmd, **_: subprocess.CompletedProcess(cmd, 2)
    )
    monkeypatch.setattr(module, "api_requirements", lambda: [])
    output = tmp_path / "out.txt"
    env = {"GITHUB_REPOSITORY": "me/r", "GITHUB_OUTPUT": str(output)}

    assert module.export_static_site(out=tmp_path / "site", base_path="/", env=env) == 0

    assert not output.exists()
    assert "export-site failed" in capsys.readouterr().err


def test_api_requirements_reads_the_installed_extra() -> None:
    reqs = _wrapper().api_requirements()
    assert any(r.startswith("fastapi") for r in reqs)
    assert all("extra" not in r for r in reqs)


def test_run_cases_are_read_once_per_run_and_the_cache_ends_with_the_export() -> None:
    """The per-test pages reread every run's cases per test; the export reads each run once."""
    from qasight.services import flaky_service
    from qasight_api.static_site import _cache_run_cases

    original = flaky_service._cases_for_run
    loads: list[str] = []

    class _Run:
        run_id = "r1"

    def fake_load(run: object) -> dict[str, dict[str, object]]:
        loads.append(run.run_id)  # type: ignore[attr-defined]
        return {"t": {"status": "passed"}}

    flaky_service._cases_for_run = fake_load  # type: ignore[assignment]
    try:
        with _cache_run_cases():
            first = flaky_service._cases_for_run(_Run())  # type: ignore[arg-type]
            assert flaky_service._cases_for_run(_Run()) is first  # type: ignore[arg-type]
        assert loads == ["r1"]
        assert flaky_service._cases_for_run is fake_load
    finally:
        flaky_service._cases_for_run = original  # type: ignore[assignment]
