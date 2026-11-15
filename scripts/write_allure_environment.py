from __future__ import annotations

import os
import platform
import subprocess  # nosec B404 - runs test tools; every call passes an argv list without a shell
import sys
from pathlib import Path


def _git(cmd: list[str], *, cwd: Path) -> str | None:
    try:
        p = subprocess.run(["git", *cmd], cwd=str(cwd), capture_output=True, text=True, check=False)  # nosec B603 B607 - argv list, no shell; the tool is found on PATH
        out = (p.stdout or "").strip()
        return out or None
    except Exception:
        return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    results_dir = root / "allure-results" / "pytest"
    results_dir.mkdir(parents=True, exist_ok=True)

    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root) or "unknown"
    sha = _git(["rev-parse", "HEAD"], cwd=root) or "unknown"

    lines = [
        f"python.version={platform.python_version()}",
        f"python.executable={sys.executable}",
        f"os.name={os.name}",
        f"platform.system={platform.system()}",
        f"platform.release={platform.release()}",
        f"platform.version={platform.version()}",
        f"platform.machine={platform.machine()}",
        f"git.branch={branch}",
        f"git.sha={sha}",
    ]
    (results_dir / "environment.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Copy the failure categories next to the results for Allure CLI pickup.
    src_categories = Path(__file__).with_name("allure_categories.json")
    if src_categories.exists():
        (results_dir / "categories.json").write_text(
            src_categories.read_text(encoding="utf-8"), encoding="utf-8"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
