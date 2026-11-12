"""Immutable, framework-agnostic dataclasses for the cycle-aware config schema.

The CLI loader produces a :class:`QASightConfig` by parsing YAML or the
``[tool.qasight]`` table of ``pyproject.toml``.  Down-stream code (the
orchestrator, the report exporter, tests) consumes these objects directly and
never re-parses the source text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from qasight.config.affected import AffectedConfig
from qasight.config.history_section import HistoryConfig

if TYPE_CHECKING:
    from qasight.resources.pools import ResourcePool, StageResources

# Supported frameworks. Add new ones in :mod:`qasight.frameworks` and append here.
SUPPORTED_FRAMEWORKS: frozenset[str] = frozenset(
    {"pytest", "behave", "behavex", "command", "junit", "playwright", "jest", "vitest", "cypress"}
)

# Frameworks that act on a stage's ``workers``: BehaveX always (``--parallel-processes``),
# Playwright always (``--workers``), Jest and Vitest always (``--maxWorkers``),
# pytest only when pytest-xdist is installed (``-n``).
# The others run single-process.
PARALLEL_FRAMEWORKS: frozenset[str] = frozenset(
    {"pytest", "behavex", "playwright", "jest", "vitest"}
)

# Test-pyramid tiers a stage's tests are counted under. See `pyramid_data.py`.
SUPPORTED_TIERS: frozenset[str] = frozenset({"unit", "integration", "e2e"})

# Default tier per framework when a stage doesn't set 'tier' explicitly.
DEFAULT_TIER_BY_FRAMEWORK: dict[str, str] = {
    "pytest": "unit",
    "behave": "integration",
    "behavex": "e2e",
    "playwright": "e2e",
    "jest": "unit",
    "vitest": "unit",
    "cypress": "e2e",
    # Any other runner (Maestro, go test, dotnet test, ...): the stage's
    # ``args`` are the whole argv; results come in through ``junit_xml``.
    "command": "unit",
    # Runs nothing: records the JUnit XML its ``junit_xml`` globs match (``qasight ingest``).
    "junit": "unit",
}

# Supported post-run reporters. Add new ones in :mod:`qasight.reporting.reporters` and append here.
SUPPORTED_REPORTER_TYPES: frozenset[str] = frozenset(
    {"allure", "extent", "otel", "reportportal", "slack", "teams", "testbeats"}
)


@dataclass(frozen=True)
class Defaults:
    """Repository-wide defaults inherited by every stage unless overridden."""

    target_repo: Path = Path(".")
    artifacts_root: Path = Path("artifacts")
    timeout_s: float | None = 600.0
    workers: int = 4
    extra_env: tuple[tuple[str, str], ...] = ()
    # True when ``defaults:`` sets ``workers`` itself (not the built-in 4).
    workers_explicit: bool = False


@dataclass(frozen=True)
class CycleTrigger:
    """Optional selective execution: run the cycle only when matched paths change.

    ``paths`` use pathlib-style globs relative to the config file directory (anchor).
    """

    paths: tuple[str, ...]
    since_ref: str | None = None  # e.g. ``origin/main`` → ``git diff since_ref...HEAD``


@dataclass(frozen=True)
class StageServe:
    """``serve:``: the app a stage's tests need, started before the stage and stopped after.

    *command* runs through the shell in the stage's ``target_repo``; the stage starts
    once *url* answers (any HTTP status below 500), or fails after *timeout_s*.
    """

    command: str
    url: str
    timeout_s: float = 120.0


@dataclass(frozen=True)
class Stage:
    """One execution step in a plan.

    All path-typed fields are resolved against the config file's directory at
    load time so the orchestrator never has to know where the YAML lives.
    """

    name: str
    framework: str
    target_repo: Path
    args: tuple[str, ...] = ()
    workers: int = 4
    timeout_s: float | None = 600.0
    if_expr: str | None = None
    extra_env: tuple[tuple[str, str], ...] = ()
    tier: str = "unit"
    # Glob patterns (relative to ``target_repo``) of JUnit XML reports the stage
    # writes. After the stage they are converted into Allure ``*-result.json``
    # files in the stage's results dir, so non-Allure runners count in the
    # summaries, health % and reports like any other stage.
    junit_xml: tuple[str, ...] = ()
    # ``resources:``: the pool its workers lease keys from (qasight/resources/).
    resources: StageResources | None = None
    # ``workers: auto``: as many workers as the pool has free keys when the stage starts.
    workers_auto: bool = False
    # True when the stage or ``defaults:`` sets ``workers``. A pytest stage on the
    # built-in default runs serially without a warning when pytest-xdist is missing.
    workers_explicit: bool = True
    # ``serve:``: the app to start first (Cypress and other end-to-end runners).
    serve: StageServe | None = None


@dataclass(frozen=True)
class ReporterSpec:
    """One post-run reporter entry from the top-level ``reporters:`` list."""

    type: str
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class QuarantineRule:
    """One ``quarantine:`` entry: a test whose failures don't fail its stage.

    ``test`` is an ``fnmatch`` glob matched against each failed test's name and
    full name. Quarantined tests still run and are still reported; see
    :mod:`qasight.engine.quarantine`.
    """

    test: str
    reason: str | None = None
    owner: str | None = None


@dataclass(frozen=True)
class Plan:
    """A named list of stages run by ``qasight run --cycle <name>``.

    Stages run in order, one at a time, unless ``max_parallel_stages`` is above 1:
    then up to that many independent stages run at once (see ``engine/orchestrator.py``).
    """

    name: str
    description: str | None
    stages: tuple[Stage, ...]
    trigger: CycleTrigger | None = None
    # Top-level ``quarantine:`` entries followed by the cycle's own.
    quarantine: tuple[QuarantineRule, ...] = ()
    max_parallel_stages: int = 1


@dataclass(frozen=True)
class QASightConfig:
    """Top-level configuration object produced by the loader."""

    version: int
    defaults: Defaults
    cycles: dict[str, Plan] = field(default_factory=dict)
    reporters: tuple[ReporterSpec, ...] = ()
    source_path: Path | None = None
    # Top-level ``resources:``: pools of keys parallel workers lease (qasight/resources/).
    resources: dict[str, ResourcePool] = field(default_factory=dict)
    # Top-level ``affected:``: how changed files map to tests (`qasight affected`).
    affected: AffectedConfig = field(default_factory=AffectedConfig)
    # Top-level ``history:``: how long run history is kept (`qasight clean --history`).
    history: HistoryConfig = field(default_factory=HistoryConfig)
    # Keys the loader ignored ("unknown key 'cycles.web.triger' is ignored; did you mean ...").
    warnings: tuple[str, ...] = ()

    @property
    def plans(self) -> dict[str, Plan]:
        """Backward-compatible alias for older call sites."""
        return self.cycles


# The class's name before the rename to QASight (1.6).
TestosteroneConfig = QASightConfig
