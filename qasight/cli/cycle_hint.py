"""Say when a cycle name has no runs at all (P10 in docs/engine-cli-review.md).

``qasight badge --cycle wbe`` and ``qasight flaky --cycle wbe`` said "no runs", the
same as a real cycle before its first run. When no run in the history has that
cycle name, this adds which cycles do, and a guess.
"""

from __future__ import annotations

import difflib


def unknown_cycle_hint(cycle: str | None) -> str | None:
    """A sentence to add when *cycle* has no runs in the history, or None."""
    if not cycle:
        return None
    try:
        from qasight.history.read_model import latest_run_per_cycle

        known = sorted(latest_run_per_cycle())
    except Exception:  # noqa: BLE001 — no database: nothing to compare against
        return None
    if cycle in known:
        return None
    if not known:
        return f"no cycle has any recorded runs yet (looked for {cycle!r})."
    guess = difflib.get_close_matches(cycle, known, n=1)
    shown = ", ".join(known[:10]) + (", ..." if len(known) > 10 else "")
    hint = f"; did you mean {guess[0]!r}?" if guess else "."
    return f"no cycle named {cycle!r} has any runs (cycles with runs: {shown}){hint}"
