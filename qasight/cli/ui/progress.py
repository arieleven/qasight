"""Live Rich :class:`Progress` heartbeat for the stage currently running.

The orchestrator runs stages sequentially, so a single, transient progress
display is enough.  It deliberately does NOT print stage log lines — those
are captured into the log buffer and flushed in the post-mortem panel.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


@contextmanager
def stage_progress(console: Console, *, label: str) -> Iterator[Progress]:
    """Yield a transient :class:`Progress` instance for one stage."""
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[muted]{task.fields[label]}[/]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        progress.add_task("stage", label=label, total=None)
        yield progress
