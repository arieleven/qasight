"""``npx qasight``: the npm package that runs the CLI through uv (integrations/npm-qasight)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[3] / "integrations" / "npm-qasight"
_NODE = shutil.which("node")

needs_node = pytest.mark.skipif(_NODE is None or sys.platform == "win32", reason="needs node")


def _run(tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "uv"
    fake.write_text('#!/bin/sh\necho "uv $*"\nexit 7\n', encoding="utf-8")
    fake.chmod(0o755)
    node_dir = str(Path(str(_NODE)).parent)
    return subprocess.run(  # noqa: S603
        [str(_NODE), str(_PKG / "bin" / "qasight.js"), *args],
        env={
            "PATH": os.pathsep.join([str(bin_dir), node_dir, "/usr/bin", "/bin"]),
            "HOME": str(tmp_path),
            **env,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@needs_node
def test_runs_the_cli_through_uv_and_keeps_its_exit_code(tmp_path: Path) -> None:
    result = _run(tmp_path, "run", "--cycle", "web", "--ci")
    assert result.stdout.strip() == "uv tool run --from qasight qasight run --cycle web --ci"
    assert result.returncode == 7


@needs_node
def test_version_pin_and_the_web_ui_extra(tmp_path: Path) -> None:
    pinned = _run(tmp_path, "ui", QASIGHT_VERSION="1.6.0")
    assert pinned.stdout.strip() == "uv tool run --from qasight[api]==1.6.0 qasight ui"
    latest = _run(tmp_path, "init", QASIGHT_VERSION="latest")
    assert latest.stdout.strip() == "uv tool run --from qasight@latest qasight init"


@needs_node
def test_an_explicit_uv_wins_over_path(tmp_path: Path) -> None:
    other = tmp_path / "other-uv"
    other.write_text('#!/bin/sh\necho "other $*"\n', encoding="utf-8")
    other.chmod(0o755)
    result = _run(tmp_path, "--version", QASIGHT_UV=str(other))
    assert result.stdout.startswith("other tool run")
    assert result.returncode == 0


def test_package_manifest_points_at_the_wrapper() -> None:
    manifest = json.loads((_PKG / "package.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "qasight"
    assert manifest["bin"] == {"qasight": "bin/qasight.js"}
    assert manifest["license"] == "MIT"
    assert (
        (_PKG / "bin" / "qasight.js").read_text(encoding="utf-8").startswith("#!/usr/bin/env node")
    )
