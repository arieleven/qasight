"""Abstract reporter interface and shared context types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from qasight.reporting.collector import CollectedResults

if TYPE_CHECKING:
    from rich.console import Console

ReportLayout = Literal["cycle", "docker_run"]


@dataclass(frozen=True)
class ReportContext:
    """Runtime context passed to every reporter after test execution."""

    artifacts_root: Path
    plan_name: str | None = None
    layout: ReportLayout = "cycle"
    run_id: str | None = None
    ci: bool = False
    generate_only: bool = False
    inject_history: bool = True
    trend_depth: int = 1
    out_dir: Path | None = None
    run_report_root: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    open_browser: bool = True


@dataclass(frozen=True)
class ReporterResult:
    ok: bool
    message: str
    artifacts: tuple[Path, ...] = field(default_factory=tuple)


class BaseReporter(ABC):
    """Standard interface for publishing raw test artifacts."""

    def __init__(self, *, options: dict[str, str] | None = None) -> None:
        self._options = options or {}

    @property
    @abstractmethod
    def reporter_type(self) -> str:
        """Registry key (e.g. ``allure``, ``extent``)."""

    @abstractmethod
    def publish(
        self,
        *,
        results: CollectedResults,
        context: ReportContext,
        console: Console | None = None,
    ) -> ReporterResult:
        """Consume collected artifacts and emit or upload a report."""
