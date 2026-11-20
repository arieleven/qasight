"""One rule for "which cycle?" when a command isn't told.

``qasight flaky``, ``split``, ``watch``, ``issues sync`` and ``quarantine add`` used to
require ``--cycle`` even in a project with one cycle, while ``summary`` and
``badge`` used "the newest run of any cycle" and ``init`` the folder name. Now every
command that reads history picks the cycle the same way:

1. ``--cycle``;
2. ``$TESTO_CYCLE`` (``qasight run`` sets it for its stages) or ``$QASIGHT_CYCLE``;
3. the only cycle in ``qasight.yaml``;
4. the cycle of the newest recorded run in this project.

When a guess was made (3 or 4), one muted line on stderr says which cycle and why,
so a person or an agent reading the output can tell. With nothing to pick from,
the command exits 2 and lists the cycles there are.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import typer

CYCLE_ENV_VARS: tuple[str, ...] = ("TESTO_CYCLE", "QASIGHT_CYCLE")


@dataclass(frozen=True)
class PickedCycle:
    name: str
    why: str  # "", "only cycle in qasight.yaml", "newest recorded run", "$TESTO_CYCLE"


def _config_cycles(config: Path | None) -> list[str]:
    try:
        from qasight.config.loader import discover_and_load

        return sorted(discover_and_load(config_path=config).cycles)
    except Exception:  # noqa: BLE001 — no config, or a broken one: fall through to the history
        return []


def _newest_recorded_cycle() -> str | None:
    try:
        from qasight.history.read_model import latest_run_per_cycle

        latest = latest_run_per_cycle()  # newest cycle first
    except Exception:  # noqa: BLE001 — no database yet
        return None
    return next(iter(latest), None)


def pick_cycle(explicit: str | None, *, config: Path | None = None) -> PickedCycle | None:
    """The cycle to use, following the rule above, or ``None`` when there is none."""
    name = (explicit or "").strip()
    if name:
        return PickedCycle(name, "")
    for var in CYCLE_ENV_VARS:
        value = (os.environ.get(var) or "").strip()
        if value:
            return PickedCycle(value, f"${var}")
    cycles = _config_cycles(config)
    if len(cycles) == 1:
        return PickedCycle(cycles[0], "only cycle in qasight.yaml")
    newest = _newest_recorded_cycle()
    if newest:
        return PickedCycle(newest, "newest recorded run")
    return None


def require_cycle(explicit: str | None, *, config: Path | None = None, quiet: bool = False) -> str:
    """:func:`pick_cycle`, or exit 2 with the cycles to choose from."""
    picked = pick_cycle(explicit, config=config)
    if picked is None:
        cycles = _config_cycles(config)
        listed = f" Cycles in qasight.yaml: {', '.join(cycles)}." if cycles else ""
        typer.echo(
            "error: no cycle to use: pass --cycle NAME (no runs are recorded yet "
            f"and qasight.yaml doesn't have exactly one cycle).{listed}",
            err=True,
        )
        raise typer.Exit(code=2)
    if picked.why and not quiet:
        print(f"cycle: {picked.name} ({picked.why})", file=sys.stderr)
    return picked.name
