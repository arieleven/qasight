"""integrations/testo-core: the package's name before 1.6, kept as a thin alias of qasight."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "integrations" / "testo-core"


def _main() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _compat() -> dict:
    return tomllib.loads((PKG / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_it_is_released_at_qasights_version_and_pins_it() -> None:
    version = _main()["version"]
    compat = _compat()
    assert compat["name"] == "testo-core"
    assert compat["version"] == version
    assert compat["dependencies"] == [f"qasight=={version}"]


def test_every_extra_forwards_to_the_same_qasight_extra() -> None:
    version = _main()["version"]
    extras = _compat()["optional-dependencies"]
    assert set(extras) == set(_main()["optional-dependencies"])
    for name, deps in extras.items():
        assert deps == [f"qasight[{name}]=={version}"], name


def test_it_writes_the_old_commands_too() -> None:
    # Upgrading from testo-core 1.5 deletes the old package's `testo` script after
    # qasight wrote its own copy, so the compat package must write it again.
    main_scripts = _main()["scripts"]
    compat_scripts = _compat()["scripts"]
    for old in ("testo", "testo-api", "testo-runner-service"):
        assert compat_scripts[old] == main_scripts[old]


def test_publish_workflow_builds_and_checks_it() -> None:
    wf = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "python -m build integrations/testo-core --outdir dist" in wf
    assert "integrations/testo-core/pyproject.toml" in wf


def test_old_imports_resolve_to_the_same_modules() -> None:
    code = (
        "import warnings\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    import testo_core, qasight\n"
        "    import testo_core.engine.exit_codes as old\n"
        "import qasight.engine.exit_codes as new\n"
        "from testo_core import TestosteroneConfig, QASightConfig\n"
        "assert testo_core is qasight and old is new\n"
        "assert TestosteroneConfig is QASightConfig\n"
        "assert any(issubclass(w.category, DeprecationWarning) for w in caught)\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={"PYTHONPATH": f"{PKG}{os.pathsep}{ROOT}", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
