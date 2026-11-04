"""``qasight watch`` — debounced filesystem watcher that re-runs a cycle."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode

_IGNORE_DIR_PARTS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "artifacts",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
    }
)


def _should_ignore(path: Path) -> bool:
    try:
        return any(part in _IGNORE_DIR_PARTS for part in path.parts)
    except (OSError, ValueError):
        return True


def watch(
    cycle: str = typer.Option(
        ...,
        "--cycle",
        "-c",
        help="Cycle name to pass to qasight run on each change batch.",
    ),
    config: Path | None = typer.Option(
        None,
        "--config",
        help="Path to qasight.yaml (passed through to qasight run).",
    ),
    debounce_ms: int = typer.Option(
        750,
        "--debounce-ms",
        help="Quiet period after the last filesystem event before running the cycle.",
    ),
    watch_root: Path = typer.Option(
        Path("."),
        "--path",
        "-p",
        help="Directory tree to watch (recursive).",
    ),
) -> None:
    """Watch the repo and re-invoke ``qasight run --cycle …`` after changes (TDD-style)."""
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    from qasight.cli.runner import execute_plan_command
    from qasight.cli.ui.console import default_console

    console = default_console()
    root = watch_root.expanduser().resolve()
    if not root.is_dir():
        console.print(f"[fail]Watch path is not a directory: {root}[/]")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    debounce_s = max(0.05, float(debounce_ms) / 1000.0)
    timer_lock = threading.Lock()
    timer: list[threading.Timer | None] = [None]

    def _run_cycle() -> None:
        code = execute_plan_command(
            console=console,
            plan_name=cycle,
            config_path=config,
            stream=False,
            ci=False,
            persist=True,
            workers_override=None,
            force=False,
            report_db=True,
            async_report_db=False,
        )
        if code != 0:
            console.print(f"[warn]Run exited with code {code}.[/]")
        else:
            console.print(f"[ok]Cycle {cycle!r} completed.[/]")

    class _Handler(FileSystemEventHandler):
        def on_any_event(self, event):  # noqa: ANN001
            if event.is_directory:
                return
            src = getattr(event, "src_path", "") or ""
            if not src:
                return
            try:
                p = Path(src).resolve()
            except OSError:
                return
            if _should_ignore(p):
                return

            def _fire() -> None:
                with timer_lock:
                    timer[0] = None
                console.print(f"[muted]Change detected:[/] {p} — running cycle {cycle!r} …")
                _run_cycle()

            with timer_lock:
                if timer[0] is not None:
                    timer[0].cancel()
                timer[0] = threading.Timer(debounce_s, _fire)
                timer[0].daemon = True
                timer[0].start()

    console.print(f"[ok]Watching[/] {root} — Ctrl+C to stop.")
    obs = Observer()
    obs.schedule(_Handler(), str(root), recursive=True)
    obs.start()
    try:
        while obs.is_alive():
            time.sleep(0.25)
    except KeyboardInterrupt:
        console.print("[muted]Stopping watcher…[/]")
    finally:
        obs.stop()
        obs.join(timeout=5)
    raise typer.Exit(code=0)
