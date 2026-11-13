from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path("action.yml")
SUBFOLDER = Path("integrations/github-action/action.yml")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_subfolder_action_is_generated_from_the_root_one() -> None:
    """One action, edited in one place: the older subfolder path is generated from the root."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "scripts/sync_action_copy.py", "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    root, sub = _load(ROOT), _load(SUBFOLDER)
    run_step = "integrations/github-action/run_testo_action.py"
    for step in root["runs"]["steps"]:
        if "run" in step:
            step["run"] = step["run"].replace(f"/{run_step}", "/run_testo_action.py")
    assert root == sub


def test_root_action_is_ready_for_the_marketplace() -> None:
    root = _load(ROOT)
    assert root["branding"]["icon"] and root["branding"]["color"]
    # Short enough to show in full on the Marketplace card.
    assert len(root["description"]) <= 125


def test_inputs_never_pasted_into_a_shell_script() -> None:
    """``${{ inputs.x }}`` inside ``run:`` is script injection when a caller passes a
    branch name or PR title; inputs go through ``env:`` instead."""
    for path in (ROOT, SUBFOLDER):
        for step in _load(path)["runs"]["steps"]:
            assert "${{" not in step.get("run", ""), (path, step.get("name"))
