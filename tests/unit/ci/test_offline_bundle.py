"""scripts/offline/: pack on a machine with internet, install on one without.

pip and docker are stand-ins that log their arguments, so nothing touches the network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BUNDLE = REPO / "scripts" / "offline" / "bundle.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

FAKE_PIP = """#!/usr/bin/env bash
echo "pip $*" >> "$LOG"
dest=""
while [ $# -gt 0 ]; do [ "$1" = --dest ] && dest="$2"; shift; done
: > "$dest/qasight-0.0.0-py3-none-any.whl"
"""

FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$LOG"
if [ "$1" = save ]; then : > "$3"; fi
"""


def _tools(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("fake-pip", FAKE_PIP), ("docker", FAKE_DOCKER)):
        tool = bin_dir / name
        tool.write_text(body)
        tool.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PIP": str(bin_dir / "fake-pip"),
        "LOG": str(tmp_path / "calls.log"),
    }


def _bundle(tmp_path: Path, env: dict[str, str], *args: str) -> Path:
    out = tmp_path / "out"
    subprocess.run(
        ["bash", str(BUNDLE), "--version", "9.9.9", "--out", str(out), *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return out / "qasight-offline-9.9.9-linux-amd64.tar.gz"


def test_bundle_packs_wheels_images_template_and_installer(tmp_path: Path) -> None:
    env = _tools(tmp_path)
    archive = _bundle(tmp_path, env, "--python", "3.11,3.12", "--runner")

    with tarfile.open(archive) as tar:
        names = {
            Path(n).relative_to("qasight-offline-9.9.9-linux-amd64").as_posix()
            for n in tar.getnames()[1:]
        }
    assert {
        "install.sh",
        "bundle.env",
        "SHA256SUMS",
        "gitlab/qasight.gitlab-ci.yml",
        "images/qasight-server-9.9.9.tar",
        "images/qasight-runner-9.9.9.tar",
        "images/qasight-runner-service-9.9.9.tar",
        "images/postgres-16-alpine.tar",
    } <= names

    calls = Path(env["LOG"]).read_text()
    # Wheels for the target (each Python, Linux x86_64), never source builds.
    assert calls.count("--only-binary=:all:") == 2
    assert "--python-version 3.11" in calls and "--python-version 3.12" in calls
    assert "manylinux2014_x86_64" in calls
    assert "qasight[api,db]==9.9.9" in calls
    assert (
        "docker pull --quiet --platform linux/amd64 ghcr.io/arieleven/qasight-server:9.9.9" in calls
    )


def test_no_images_needs_no_docker(tmp_path: Path) -> None:
    env = _tools(tmp_path)
    archive = _bundle(tmp_path, env, "--no-images")
    with tarfile.open(archive) as tar:
        assert not [n for n in tar.getnames() if n.endswith(".tar")]
    assert not [c for c in Path(env["LOG"]).read_text().splitlines() if c.startswith("docker ")]


def test_install_checks_sums_loads_and_pushes_to_a_registry(tmp_path: Path) -> None:
    env = _tools(tmp_path)
    archive = _bundle(tmp_path, env)
    inside = tmp_path / "inside"
    with tarfile.open(archive) as tar:
        tar.extractall(inside, filter="data")
    folder = inside / "qasight-offline-9.9.9-linux-amd64"
    Path(env["LOG"]).write_text("")

    result = subprocess.run(
        ["bash", "install.sh", "--no-cli", "--registry", "registry.example.local/qa/"],
        cwd=folder,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    calls = Path(env["LOG"]).read_text()
    assert "docker load --quiet -i images/qasight-server-9.9.9.tar" in calls
    assert (
        "docker tag ghcr.io/arieleven/qasight-server:9.9.9 registry.example.local/qa/qasight-server:9.9.9"
        in calls
    )
    assert "docker push --quiet registry.example.local/qa/postgres:16-alpine" in calls
    assert "TESTO_REGISTRY=registry.example.local/qa" in result.stdout
    assert "TESTO_POSTGRES_IMAGE=registry.example.local/qa/postgres:16-alpine" in result.stdout

    # A file changed on the way in stops the install before anything is loaded.
    (folder / "gitlab" / "qasight.gitlab-ci.yml").write_text("tampered\n")
    broken = subprocess.run(
        ["bash", "install.sh", "--no-cli"], cwd=folder, env=env, capture_output=True, text=True
    )
    assert broken.returncode != 0


def test_compose_images_come_from_a_configurable_registry() -> None:
    compose = yaml.safe_load((REPO / "deploy" / "server" / "docker-compose.yml").read_text())
    services = compose["services"]
    assert services["server"]["image"].startswith("${TESTO_REGISTRY:-ghcr.io/arieleven}/")
    assert services["postgres"]["image"] == "${TESTO_POSTGRES_IMAGE:-postgres:16-alpine}"
    env = services["server"]["environment"]
    for name in (
        "TESTO_NO_UPDATE_CHECK",
        "TESTO_AI_PROVIDER",
        "TESTO_AI_BASE_URL",
        "TESTO_AI_MODEL",
    ):
        assert name in env
