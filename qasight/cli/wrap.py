"""``qasight run -- <command>``: run a test command, then record its results like ``qasight ingest``."""

from __future__ import annotations

import os
import time
from pathlib import Path

from qasight.engine.exit_codes import EngineExitCode


def run_wrapped(
    command: list[str],
    *,
    cycle: str | None,
    config: Path | None,
    ci: bool,
    persist: bool,
    coverage: list[str],
) -> int:
    """Run *command* here, record the results it wrote; returns the command's exit code."""
    from qasight.cli.runner import execute_ingest_command
    from qasight.cli.ui.console import default_console, make_console
    from qasight.services import wrap_run
    from qasight.services.project_setup import cycle_name_for

    console = make_console(plain=True) if ci else default_console()
    cwd = Path.cwd().resolve()
    name = (cycle or "").strip() or cycle_name_for(cwd)
    wrapped = wrap_run.prepare(command, cwd=cwd, out_dir=wrap_run.new_out_dir(cwd))
    if not ci:
        added = [a for a in wrapped.argv if a not in command and a != "--"]
        how = f" (adds {' '.join(added)})" if added else ""
        console.print(f"[muted]qasight: running {wrapped.label}{how}[/]", highlight=False)
    started = time.time() - 1  # file times can round down
    try:
        code = wrap_run.run(wrapped, cwd=cwd, env=dict(os.environ))
    except KeyboardInterrupt:
        console.print("\nqasight: stopped; nothing recorded.")
        return int(EngineExitCode.INTERRUPTED)

    files = wrap_run.result_files(wrapped, cwd=cwd, since=started)
    if not files:
        console.print(
            "[warn]qasight: the command left no result file to record.[/] "
            "Use a runner QASight knows (pytest, Jest, Vitest, Playwright, go test, dotnet test, "
            "Maven, Gradle, RSpec, PHPUnit), or make yours write JUnit XML."
        )
        return code
    reports = [os.path.relpath(p, cwd) for p in files]
    recorded = execute_ingest_command(
        console=console,
        reports=reports,
        cycle=name,
        stage=wrapped.runner or "tests",
        tier=wrapped.tier,
        repo=cwd,
        config_path=config,
        ci=ci,
        persist=persist,
        coverage=coverage,
    )
    if recorded not in (int(EngineExitCode.SUCCESS), int(EngineExitCode.DOMAIN_FAILURE)):
        console.print(f"[warn]qasight: recording the results failed (exit {recorded}).[/]")
        return code or recorded
    return code
