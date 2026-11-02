"""``testo ui --from-github`` (testo_core/services/github_history.py) with a fake ``gh``."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testo_api import ui as ui_mod
from testo_core import ui_bundle
from testo_core.cli.app import app
from testo_core.services import github_history as gh

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

_ARTIFACTS = {
    "artifacts": [
        {"name": "coverage", "created_at": "2026-11-01T12:00:00Z", "workflow_run": {"id": 9}},
        {
            "name": "testo-history-ci-tests-7-1-run",
            "created_at": "2026-11-01T10:00:00Z",
            "workflow_run": {"id": 7},
        },
        {
            "name": "testo-history-ci-tests-8-1-run",
            "created_at": "2026-11-01T11:00:00Z",
            "workflow_run": {"id": 8},
        },
        {
            "name": "testo-history-ci-tests-9-1-run",
            "created_at": "2026-11-01T13:00:00Z",
            "workflow_run": {"id": 9},
            "expired": True,
        },
    ]
}


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``gh`` that answers ``api`` with _ARTIFACTS and ``run download`` with a database."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "artifacts.json").write_text(json.dumps(_ARTIFACTS))
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{bin_dir}/calls"\n'
        'if [ "$1" = api ]; then cat "' + str(bin_dir) + '/artifacts.json"; exit 0; fi\n'
        'while [ $# -gt 0 ]; do [ "$1" = --dir ] && dir="$2"; shift; done\n'
        'printf db > "$dir/testo_history.db"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def test_the_newest_unexpired_history_artifact_is_downloaded(fake_gh: Path, tmp_path: Path) -> None:
    db, artifact = gh.download_history(cwd=tmp_path, dest=tmp_path / "dl", repo="o/r")

    assert artifact.name == "testo-history-ci-tests-8-1-run" and artifact.run_id == "8"
    assert db.read_text() == "db"
    calls = (fake_gh / "calls").read_text().splitlines()
    assert calls[0] == "api repos/o/r/actions/artifacts?per_page=100"
    assert calls[1].startswith("run download 8 --name testo-history-ci-tests-8-1-run --dir ")
    assert calls[1].endswith("--repo o/r")


def test_a_run_id_asks_for_that_runs_artifacts(fake_gh: Path, tmp_path: Path) -> None:
    gh.find_artifact(cwd=tmp_path, repo=None, run_id="8")
    assert (fake_gh / "calls").read_text().split()[1] == (
        "repos/{owner}/{repo}/actions/runs/8/artifacts?per_page=100"
    )


def test_no_artifact_says_why(fake_gh: Path, tmp_path: Path) -> None:
    (fake_gh / "artifacts.json").write_text('{"artifacts": []}')
    with pytest.raises(gh.GithubHistoryError, match="history-artifact"):
        gh.find_artifact(cwd=tmp_path, repo=None, run_id=None)


def test_a_bad_repo_is_refused_before_calling_gh(tmp_path: Path) -> None:
    with pytest.raises(gh.GithubHistoryError, match="owner/name"):
        gh.find_artifact(cwd=tmp_path, repo="o/r; rm -rf /", run_id=None)


def test_ui_from_github_points_the_server_at_the_download(
    fake_gh: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(ui_bundle, "bundled_ui_dir", lambda: tmp_path / "ui")
    monkeypatch.delenv(ui_mod.UI_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in ("DATABASE_URL", "TESTO_HOME"):
        monkeypatch.delenv(key, raising=False)
    seen: dict[str, str] = {}

    def fake_run(argv: list[str]) -> int:  # noqa: ARG001
        seen.update(db=os.environ["DATABASE_URL"], home=os.environ["TESTO_HOME"])
        return 0

    import testo_api.main

    monkeypatch.setattr(testo_api.main, "run", fake_run)
    result = CliRunner().invoke(app, ["ui", "--no-open", "--from-github", "--repo", "o/r"])

    assert result.exit_code == 0, result.output
    assert "testo-history-ci-tests-8-1-run" in result.output
    assert seen["db"].startswith("sqlite:///") and seen["db"].endswith("/testo_history.db")
    assert seen["home"].endswith("from-github/o-r")


def test_repo_without_from_github_is_refused() -> None:
    result = CliRunner().invoke(app, ["ui", "--no-open", "--repo", "o/r"])
    assert result.exit_code == 2
