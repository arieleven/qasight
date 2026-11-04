"""Rich “dashboard” layout for ``qasight summary`` / ``qasight diff`` (non ``--metrics-only``)."""

from __future__ import annotations

from collections import defaultdict

from rich import box
from rich.columns import Columns
from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from qasight.repository.models import ReportArchive
from qasight.services.report_archive_diff import CaseChange

_DURATION_SLOW_MS = 100
_BAR_WIDTH = 42


def human_duration_ms(ms: int | None) -> str:
    """Human-readable duration from milliseconds."""

    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms} ms"
    sec_f = ms / 1000.0
    if sec_f < 60.0:
        return f"{sec_f:.1f}s"
    sec_i = int(round(sec_f))
    m, s = divmod(sec_i, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m2 = divmod(m, 60)
    return f"{h}h {m2}m {s:02d}s"


def pass_rate_percent(passed: int | None, total: int | None) -> float | None:
    if passed is None or total is None or total <= 0:
        return None
    return 100.0 * float(passed) / float(total)


def suite_duration_preferred(archive: ReportArchive) -> tuple[int | None, str]:
    """Return (ms, label) preferring Allure per-test sum, else plan wall time."""

    if archive.allure_duration_ms is not None:
        return archive.allure_duration_ms, "sum of test times"
    if archive.plan_duration_ms is not None:
        return archive.plan_duration_ms, "wall time"
    return None, ""


def format_delta_ms_cell(delta: int | None) -> Text:
    """Color rules for per-test duration delta column."""

    if delta is None:
        return Text("—", style="dim")
    if delta == 0:
        return Text("-", style="dim")
    if delta > _DURATION_SLOW_MS:
        return Text(f"+{delta} ms", style="red bold")
    if delta < 0:
        return Text(f"{delta} ms", style="green")
    return Text(f"+{delta} ms", style="yellow")


def _pp_delta_text(baseline_pct: float | None, current_pct: float | None) -> Text:
    if baseline_pct is None or current_pct is None:
        return Text("—", style="dim")
    d = current_pct - baseline_pct
    if abs(d) < 0.05:
        return Text("±0.0 pp", style="dim")
    style = "green" if d > 0 else "red" if d < 0 else "dim"
    sign = "+" if d > 0 else ""
    return Text(f"{sign}{d:.1f} pp", style=style)


def _wall_delta_text(b_ms: int | None, c_ms: int | None) -> Text:
    if b_ms is None or c_ms is None:
        return Text("—", style="dim")
    d = c_ms - b_ms
    if d == 0:
        return Text("-", style="dim")
    # Slower current run = worse = red; faster = green
    if d > _DURATION_SLOW_MS:
        return Text(f"+{human_duration_ms(d)}", style="red bold")
    if d < 0:
        return Text(f"−{human_duration_ms(-d)}", style="green")
    return Text(f"+{human_duration_ms(d)}", style="yellow")


def _stacked_bar_text(
    *,
    passed: int,
    failed: int,
    broken: int,
    skipped: int,
    width: int = _BAR_WIDTH,
) -> Text:
    total = passed + failed + broken + skipped
    if total <= 0:
        return Text("░" * width, style="dim")

    w_p = (width * passed) // total
    w_f = (width * (failed + broken)) // total
    w_sk = width - w_p - w_f

    text = Text()
    if w_p:
        text.append("█" * w_p, style="green")
    if w_f:
        text.append("█" * w_f, style="red")
    if w_sk:
        text.append("█" * w_sk, style="yellow")
    return text


def _counts(archive: ReportArchive) -> tuple[int, int, int, int]:
    p = int(archive.passed or 0)
    f = int(archive.failed or 0)
    br = int(archive.broken or 0)
    sk = int(archive.skipped or 0)
    return p, f, br, sk


def render_metrics_dashboard(
    console: Console,
    *,
    baseline: ReportArchive,
    current: ReportArchive,
) -> None:
    """Header + distribution bars using DB counters (no zip unpack)."""

    b_dur_ms, b_src = suite_duration_preferred(baseline)
    c_dur_ms, c_src = suite_duration_preferred(current)
    b_rate = pass_rate_percent(baseline.passed, baseline.total_tests)
    c_rate = pass_rate_percent(current.passed, current.total_tests)

    bp, bf, bbr, bsk = _counts(baseline)
    cp, cf, cbr, csk = _counts(current)

    score_block = Text.assemble(
        ("Quality score (pass / total)\n", "bold"),
        ("  Baseline ", "dim"),
        (f"{b_rate:.1f}%" if b_rate is not None else "—", "bold" if b_rate is not None else "dim"),
        ("  →  ", "dim"),
        (f"{c_rate:.1f}%" if c_rate is not None else "—", "bold" if c_rate is not None else "dim"),
        ("  Δ ", "dim"),
        _pp_delta_text(b_rate, c_rate),
        "\n",
        ("Total suite duration\n", "bold"),
        ("  Baseline ", "dim"),
        (human_duration_ms(b_dur_ms), "default"),
        (f"  ({b_src})\n" if b_src else "\n", "dim"),
        ("  Current  ", "dim"),
        (human_duration_ms(c_dur_ms), "default"),
        (f"  ({c_src})\n" if c_src else "\n", "dim"),
        ("  Δ ", "dim"),
        _wall_delta_text(b_dur_ms, c_dur_ms),
    )

    legend = Text.assemble(
        ("█ passed", "green"),
        ("  ", "dim"),
        ("█ failed/broken", "red"),
        ("  ", "dim"),
        ("█ skipped", "yellow"),
    )
    dist = Text.assemble(
        ("Outcome mix (DB totals)\n", "bold"),
        ("  Baseline  ", "dim"),
        _stacked_bar_text(passed=bp, failed=bf, broken=bbr, skipped=bsk),
        "\n",
        ("  Current   ", "dim"),
        _stacked_bar_text(passed=cp, failed=cf, broken=cbr, skipped=csk),
        "\n",
        legend,
    )

    left = Panel(
        score_block,
        title="[bold]Scores[/]",
        border_style="green",
        padding=(0, 1),
    )
    right = Panel(
        dist,
        title="[bold]Outcome mix[/]",
        border_style="magenta",
        padding=(0, 1),
    )
    body = Columns([left, right], expand=True, equal=True)

    console.print(
        Panel(
            body,
            title="[bold white]Quality trend[/]",
            border_style="cyan",
            padding=(1, 1),
        )
    )


def _make_change_table(
    *,
    name_max_width: int,
    include_group: bool,
) -> Table:
    t = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 1))
    if include_group:
        t.add_column("Group", style="cyan", overflow="ellipsis", max_width=36, no_wrap=True)
    t.add_column("Kind", style="bold", max_width=14, overflow="ellipsis", no_wrap=True)
    t.add_column("Test", overflow="ellipsis", max_width=name_max_width, no_wrap=True)
    t.add_column("Before", style="dim")
    t.add_column("After", style="dim")
    t.add_column("Δms", justify="right")
    return t


def _fill_change_table(
    t: Table,
    rows: list[CaseChange],
    *,
    include_group: bool,
) -> None:
    for c in rows:
        row: list[RenderableType] = []
        if include_group:
            row.append(c.group)
        row.extend(
            [
                c.kind,
                c.name,
                c.baseline_status or "—",
                c.current_status or "—",
                format_delta_ms_cell(c.duration_delta_ms),
            ]
        )
        t.add_row(*row)


def render_change_sections(
    console: Console,
    *,
    changes: list[CaseChange],
    name_max_width: int = 52,
) -> None:
    """Regressions grouped by suite; fixes / removed as compact tables."""

    regress_kinds = {"regression", "status_change", "added"}
    reg_rows = sorted(
        (c for c in changes if c.kind in regress_kinds),
        key=lambda c: (c.group.lower(), c.name.lower()),
    )
    if reg_rows:
        console.print(Rule("[bold]Regressions & risk[/]", style="red"))
        by_g: dict[str, list[CaseChange]] = defaultdict(list)
        for c in reg_rows:
            by_g[c.group].append(c)
        for g in sorted(by_g.keys(), key=str.lower):
            console.print(
                f"[bold cyan]{g}[/] [dim]({len(by_g[g])} tests)[/]",
            )
            sub = _make_change_table(
                name_max_width=name_max_width,
                include_group=False,
            )
            _fill_change_table(
                sub, sorted(by_g[g], key=lambda x: x.name.lower()), include_group=False
            )
            console.print(sub)
            console.print("")

    removed = sorted((c for c in changes if c.kind == "removed"), key=lambda c: c.name.lower())
    if removed:
        console.print(Rule("[bold]Removed cases[/]", style="yellow"))
        t = _make_change_table(name_max_width=name_max_width, include_group=True)
        _fill_change_table(t, removed[:200], include_group=True)
        console.print(t)

    fixes = sorted((c for c in changes if c.kind == "fix"), key=lambda c: c.name.lower())
    if fixes:
        console.print(Rule("[bold]Fixes[/]", style="green"))
        t = _make_change_table(name_max_width=name_max_width, include_group=True)
        _fill_change_table(t, fixes[:200], include_group=True)
        console.print(t)

    if not changes:
        console.print(
            "[dim]No test changed outcome between these two runs (or their test results weren't recorded).[/]",
        )


def render_metrics_only_table(
    console: Console, *, baseline: ReportArchive, current: ReportArchive
) -> None:
    """Original flat metrics comparison (``--metrics-only``)."""

    table = Table(title="Run metrics (archive columns)", title_justify="left")
    table.add_column("metric", style="dim")
    table.add_column("baseline", justify="right")
    table.add_column("current", justify="right")
    table.add_column("delta", justify="right")
    pairs: list[tuple[str, int | None, int | None]] = [
        ("total_tests", baseline.total_tests, current.total_tests),
        ("passed", baseline.passed, current.passed),
        ("failed", baseline.failed, current.failed),
        ("broken", baseline.broken, current.broken),
        ("skipped", baseline.skipped, current.skipped),
        ("plan_duration_ms", baseline.plan_duration_ms, current.plan_duration_ms),
        ("allure_duration_ms", baseline.allure_duration_ms, current.allure_duration_ms),
    ]
    for label, bv, cv in pairs:
        d: int | None = None
        if isinstance(bv, int) and isinstance(cv, int):
            d = cv - bv
        table.add_row(
            label,
            "—" if bv is None else str(bv),
            "—" if cv is None else str(cv),
            "—" if d is None else str(d),
        )
    console.print(table)


def render_full_diff(
    console: Console,
    *,
    baseline: ReportArchive,
    current: ReportArchive,
    changes: list[CaseChange],
    metrics_only: bool,
) -> None:
    """Entry: dashboard + tables, or metrics-only table."""

    if metrics_only:
        render_metrics_only_table(console, baseline=baseline, current=current)
        return

    render_metrics_dashboard(console, baseline=baseline, current=current)
    name_w = max(32, min(72, (console.width or 100) - 46))
    render_change_sections(console, changes=changes, name_max_width=name_w)
