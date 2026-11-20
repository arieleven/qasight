"""Post-mortem Rich panel for a finished stage + plan summary table."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from qasight.reporting.metrics import parse_allure_results_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StagePanelData:
    """Data needed to render the post-mortem panel for a finished stage."""

    name: str
    framework: str
    returncode: int
    duration_s: float
    log_path: str | None
    output_tail: str
    results_dir: str | None = None
    command: str | None = None
    error: str | None = None
    # Failed tests a ``quarantine:`` rule matched (they don't fail the stage on their own).
    quarantined: tuple[str, ...] = ()
    # "Stack overflow (Python): RecursionError: ..." when crash detection named the failure.
    crash: str | None = None


def _status_markup(data: StagePanelData) -> str:
    status = "[ok]PASS[/]" if data.returncode == 0 else f"[fail]FAIL ({data.returncode})[/]"
    if data.quarantined:
        status += f" [warn]({len(data.quarantined)} quarantined)[/]"
    return status


def render_stage_panel(console: Console, data: StagePanelData, *, tail_max_lines: int = 80) -> None:
    """Render a single stage panel to ``console``."""
    style = "ok" if data.returncode == 0 else "fail"

    equipment = _format_equipment(data.framework)
    title = Text.from_markup(f"[{style}]{data.name}[/] {equipment} {_status_markup(data)}")

    rows: list[RenderableType] = []
    rows.append(Text.from_markup(f"[muted]duration:[/] {data.duration_s:.2f}s"))
    if data.command:
        rows.append(Text.from_markup(f"[muted]command:[/]  {data.command}"))
    rows.append(Text.from_markup(f"[muted]log:[/]      {data.log_path or '<buffered>'}"))
    if data.quarantined:
        rows.append(
            Text.from_markup(
                "[muted]quarantined failures:[/] " + ", ".join(escape(n) for n in data.quarantined)
            )
        )
    if data.error:
        rows.append(Text.from_markup("[fail]error:[/]    ").append(data.error))
    if data.crash:
        rows.append(Text.from_markup("[fail]crash:[/]    ").append(data.crash))

    tail = data.output_tail.rstrip("\n")
    if tail:
        lines = tail.splitlines()
        if len(lines) > tail_max_lines:
            omitted = len(lines) - tail_max_lines
            lines = [f"... ({omitted} earlier lines omitted)", *lines[-tail_max_lines:]]
        rows.append(Text(""))
        rows.append(Text("--- output tail ---", style="muted"))
        rows.append(Text("\n".join(lines)))

    console.print(Panel(Group(*rows), title=title, border_style=style, expand=True))


def render_plan_summary(
    console: Console,
    *,
    plan_name: str,
    stage_results: list[StagePanelData],
    aggregate_returncode: int,
) -> None:
    """One table for the whole cycle: each stage's status, test counts and duration.

    It used to be two tables (status, then counts) with an "Equipment" column; one
    table with the runner shown only when stages use different ones says the same
    in half the lines.
    """
    style = "ok" if aggregate_returncode == 0 else "fail"
    table = Table(
        title=Text.from_markup(f"[{style}]Cycle summary — {plan_name}[/]"),
        show_lines=False,
        title_justify="left",
    )
    show_runner = len({(s.framework or "").strip().lower() for s in stage_results}) > 1
    table.add_column("Stage", style="title")
    if show_runner:
        table.add_column("Runner", style="framework")
    table.add_column("Status")
    table.add_column("Tests", justify="right")
    table.add_column("Passed", justify="right", style="ok")
    table.add_column("Failed", justify="right", style="fail")
    table.add_column("Skipped", justify="right", style="muted")
    table.add_column("Duration", justify="right")
    for stage in stage_results:
        total = passed = failed = skipped = 0
        if stage.results_dir:
            try:
                rm = parse_allure_results_dir(Path(stage.results_dir))
                total = int(rm.total_tests)
                passed = int(rm.passed)
                failed = int(rm.failed) + int(rm.broken)
                skipped = int(rm.skipped)
            except OSError:
                logger.debug("could not read results for %s", stage.name, exc_info=True)
                total = passed = failed = skipped = 0
        cells = [stage.name]
        if show_runner:
            cells.append(_equipment_cell(stage.framework))
        cells += [
            _status_markup(stage),
            str(total),
            str(passed),
            str(failed),
            str(skipped),
            f"{stage.duration_s:.2f}s",
        ]
        table.add_row(*cells)
    console.print(table)


def _format_equipment(equipment: str) -> str:
    eq = (equipment or "").strip()
    if eq.lower() == "behavex":
        return f"[behavex]({eq})[/]"
    return f"[framework]({eq})[/]"


def _equipment_cell(equipment: str) -> str:
    eq = (equipment or "").strip()
    if eq.lower() == "behavex":
        return f"[behavex]{eq}[/]"
    return eq
