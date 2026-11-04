"""integrations/pytest-qasight: the thin ``pytest-*`` package that installs qasight."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "integrations" / "pytest-qasight"


def _project() -> dict:
    return tomllib.loads((PKG / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_name_is_listed_by_pytests_plugin_list() -> None:
    # pytest's plugin list only picks up PyPI names that start with pytest- or pytest_.
    assert _project()["name"].startswith("pytest-")


def test_it_depends_on_qasight_and_registers_no_plugin_of_its_own() -> None:
    project = _project()
    deps = project["dependencies"]
    assert any(re.match(r"qasight\b", d) for d in deps)
    assert any(re.match(r"pytest\b", d) for d in deps)
    # qasight registers pytest11 "qasight"; a second registration of it would stop pytest.
    assert "entry-points" not in project


def test_versions_agree() -> None:
    init = (PKG / "pytest_qasight" / "__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{_project()["version"]}"' in init


def test_publish_workflow_builds_this_folder() -> None:
    wf = (ROOT / ".github" / "workflows" / "publish-pytest-qasight.yml").read_text(encoding="utf-8")
    assert "integrations/pytest-qasight" in wf
    assert "environment: pypi" in wf
