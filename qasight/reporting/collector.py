"""Walk a ``qasight run`` artifacts tree and collect every Allure result dir.

The layout produced by :func:`qasight.engine.orchestrator.run_plan` is:

```
<artifacts_root>/
  <plan>/
    events.ndjson
    plan_result.json
    <stage>/
      run.log
      allure-results/
        <framework>/
          *-result.json
```

When ``plan_name`` is omitted, only the **latest** plan directory (by
``events.ndjson`` mtime) is scanned — see :func:`qasight.reporting.paths.discover_latest_plan_dir`.

The collector is **read-only** — Allure / JUnit exporters consume the
returned :class:`CollectedResults` and decide whether to copy, generate, or
stream from each entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from qasight.reporting.paths import discover_latest_plan_dir, plan_artifacts_dir


@dataclass(frozen=True)
class StageCollection:
    """One stage's raw artifacts."""

    plan: str
    stage: str
    framework: str
    results_dir: Path
    log_path: Path | None


@dataclass(frozen=True)
class CollectedResults:
    """A flat list of per-framework Allure result trees discovered on disk."""

    artifacts_root: Path
    stages: list[StageCollection] = field(default_factory=list)

    @property
    def result_dirs(self) -> list[Path]:
        return [s.results_dir for s in self.stages if s.results_dir.is_dir()]

    @property
    def by_framework(self) -> dict[str, list[Path]]:
        out: dict[str, list[Path]] = {}
        for s in self.stages:
            out.setdefault(s.framework, []).append(s.results_dir)
        return out


def collect_results(
    artifacts_root: Path,
    *,
    plan_name: str | None = None,
) -> CollectedResults:
    """Discover every per-stage Allure result directory on disk."""
    artifacts_root = artifacts_root.expanduser().resolve()
    plan_dirs: list[Path]
    if plan_name is not None:
        plan_dir = plan_artifacts_dir(artifacts_root, plan_name)
        plan_dirs = [plan_dir] if plan_dir.is_dir() else []
    else:
        latest = discover_latest_plan_dir(artifacts_root)
        plan_dirs = [latest] if latest is not None else []

    stages: list[StageCollection] = []
    for plan_dir in plan_dirs:
        plan_label = plan_dir.name
        for stage_dir in sorted(p for p in plan_dir.iterdir() if p.is_dir()):
            allure_root = stage_dir / "allure-results"
            if not allure_root.is_dir():
                continue
            log_path = stage_dir / "run.log"
            for framework_dir in sorted(p for p in allure_root.iterdir() if p.is_dir()):
                stages.append(
                    StageCollection(
                        plan=plan_label,
                        stage=stage_dir.name,
                        framework=framework_dir.name,
                        results_dir=framework_dir,
                        log_path=log_path if log_path.is_file() else None,
                    )
                )
    return CollectedResults(artifacts_root=artifacts_root, stages=stages)


def collect_results_docker_run(
    artifacts_root: Path,
    *,
    run_id: str | None = None,
) -> CollectedResults:
    """Discover Allure dirs from the flat Docker/headless layout.

    Layout::

        <artifacts_root>/allure-results/<framework>/...
        <artifacts_root>/allure-results/<framework>/<run_id>/...  (optional)
    """
    artifacts_root = artifacts_root.expanduser().resolve()
    allure_root = artifacts_root / "allure-results"
    if not allure_root.is_dir():
        return CollectedResults(artifacts_root=artifacts_root, stages=[])

    plan_label = run_id or "docker-run"
    stages: list[StageCollection] = []

    for framework_dir in sorted(p for p in allure_root.iterdir() if p.is_dir()):
        framework = framework_dir.name
        if run_id:
            scoped = framework_dir / run_id
            if scoped.is_dir() and any(scoped.glob("*-result.json")):
                stages.append(
                    StageCollection(
                        plan=plan_label,
                        stage=framework,
                        framework=framework,
                        results_dir=scoped,
                        log_path=None,
                    )
                )
                continue
        if any(framework_dir.glob("*-result.json")) or any(framework_dir.glob("**/*-result.json")):
            stages.append(
                StageCollection(
                    plan=plan_label,
                    stage=framework,
                    framework=framework,
                    results_dir=framework_dir,
                    log_path=None,
                )
            )
    return CollectedResults(artifacts_root=artifacts_root, stages=stages)
