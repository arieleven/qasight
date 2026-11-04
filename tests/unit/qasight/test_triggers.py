"""Tests for :mod:`qasight.config.triggers`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from qasight.config import triggers
from qasight.config.loader import load_config
from qasight.config.triggers import (
    evaluate_cycle_trigger,
    path_matches_trigger_glob,
    persist_trigger_snapshot,
)


def test_path_matches_trigger_glob() -> None:
    assert path_matches_trigger_glob("services/db/x.py", "services/db/**") is True
    assert path_matches_trigger_glob("services/db/x.py", "**/*.py") is True
    assert path_matches_trigger_glob("foo.py", "*.py") is True


def test_snapshot_first_run_activates(tmp_path: Path) -> None:
    """Without Git and no snapshot file, first evaluation activates."""
    anchor = tmp_path / "proj"
    anchor.mkdir()
    (anchor / "src").mkdir()
    (anchor / "src" / "a.py").write_text("x", encoding="utf-8")
    yml = anchor / "qasight.yaml"
    yml.write_text(
        """
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
cycles:
  c1:
    description: d
    trigger:
      paths:
        - "src/**/*.py"
    stages:
      - name: s1
        equipment: pytest
        args: []
""",
        encoding="utf-8",
    )
    cfg = load_config(yml)
    plan = cfg.cycles["c1"]
    assert plan.trigger is not None
    tr = evaluate_cycle_trigger(plan=plan, cfg=cfg)
    assert tr.stimulus is True
    assert tr.mode == "snapshot"
    assert tr.persist_snapshot_after_run is True


def test_snapshot_unchanged_resting(tmp_path: Path) -> None:
    anchor = tmp_path / "proj"
    anchor.mkdir()
    (anchor / "src").mkdir()
    (anchor / "src" / "a.py").write_text("x", encoding="utf-8")
    yml = anchor / "qasight.yaml"
    yml.write_text(
        """
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
cycles:
  c1:
    description: d
    trigger:
      paths:
        - "src/**/*.py"
    stages:
      - name: s1
        equipment: pytest
        args: []
""",
        encoding="utf-8",
    )
    cfg = load_config(yml)
    plan = cfg.cycles["c1"]
    assert plan.trigger is not None
    # Seed snapshot as current catalog
    persist_trigger_snapshot(
        cfg=cfg,
        plan_name=plan.name,
        anchor=yml.parent,
        patterns=plan.trigger.paths,
    )
    tr = evaluate_cycle_trigger(plan=plan, cfg=cfg)
    assert tr.stimulus is False
    assert tr.mode == "snapshot"


def test_snapshot_file_change_activates(tmp_path: Path) -> None:
    anchor = tmp_path / "proj"
    anchor.mkdir()
    (anchor / "src").mkdir()
    (anchor / "src" / "a.py").write_text("x", encoding="utf-8")
    yml = anchor / "qasight.yaml"
    yml.write_text(
        """
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
cycles:
  c1:
    description: d
    trigger:
      paths:
        - "src/**/*.py"
    stages:
      - name: s1
        equipment: pytest
        args: []
""",
        encoding="utf-8",
    )
    cfg = load_config(yml)
    plan = cfg.cycles["c1"]
    assert plan.trigger is not None
    persist_trigger_snapshot(
        cfg=cfg,
        plan_name=plan.name,
        anchor=yml.parent,
        patterns=plan.trigger.paths,
    )
    (anchor / "src" / "a.py").write_text("y", encoding="utf-8")
    tr = evaluate_cycle_trigger(plan=plan, cfg=cfg)
    assert tr.stimulus is True


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_git_trigger_matches_changed_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init = subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo, capture_output=True, text=True, timeout=30
    )
    if init.returncode != 0:
        init = subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True, timeout=30)
    if init.returncode != 0:
        pytest.skip(f"git init failed: {init.stderr}")
    subprocess.run(
        ["git", "config", "user.email", "t@test"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "services").mkdir()
    (repo / "services" / "db").mkdir(parents=True)
    (repo / "services" / "db" / "x.py").write_text("1", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    yml = repo / "qasight.yaml"
    yml.write_text(
        """
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
cycles:
  c1:
    description: d
    trigger:
      paths:
        - "services/db/**"
    stages:
      - name: s1
        equipment: pytest
        args: []
""",
        encoding="utf-8",
    )
    cfg = load_config(yml)
    plan = cfg.cycles["c1"]
    tr = evaluate_cycle_trigger(plan=plan, cfg=cfg)
    assert tr.stimulus is False
    assert tr.mode == "git"

    (repo / "services" / "db" / "x.py").write_text("2", encoding="utf-8")
    tr2 = evaluate_cycle_trigger(plan=plan, cfg=cfg)
    assert tr2.stimulus is True
    assert tr2.mode == "git"
    assert "services/db/x.py" in tr2.matched_paths


def test_git_failure_falls_back_to_snapshot_visibly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Git error must not silently turn into a snapshot run: warn and say so in the reason."""
    anchor = tmp_path / "proj"
    (anchor / "src").mkdir(parents=True)
    yml = anchor / "qasight.yaml"
    yml.write_text(
        """
version: 1
defaults:
  target_repo: .
  artifacts_root: artifacts
cycles:
  c1:
    description: d
    trigger:
      paths:
        - "src/**"
    stages:
      - name: s1
        equipment: pytest
        args: []
""",
        encoding="utf-8",
    )
    cfg = load_config(yml)

    def broken_git(**_kwargs: object) -> None:
        raise RuntimeError("bad revision 'origin/main'")

    monkeypatch.setattr(triggers, "_git_repo_root", lambda _anchor: anchor)
    monkeypatch.setattr(triggers, "_evaluate_git_trigger", broken_git)

    with caplog.at_level("WARNING", logger="qasight.config.triggers"):
        tr = evaluate_cycle_trigger(plan=cfg.cycles["c1"], cfg=cfg)

    assert tr.mode == "snapshot"
    assert tr.reason.startswith("Git evaluation failed (bad revision 'origin/main')")
    assert "falling back to snapshot mode" in caplog.text
