"""Turn what a person or an agent types for a run into a recorded run.

``qasight summary``, ``qasight runs show`` and ``qasight runs delete`` take any of:

* a full run id, or its first characters (``3ae9134f``), as long as one run matches;
* ``latest``: the newest run of the cycle (the picked cycle, see ``cycle_pick``).

``--against`` also takes ``previous`` (the run before, on the same branch),
``last-green`` (the newest earlier run that passed) and a branch name
(``main``: that branch's newest earlier run).
"""

from __future__ import annotations

from qasight.history.views import CompletedRunView

# How far back a short id or a baseline is looked for.
SEARCH_LIMIT = 500


class RunRefError(ValueError):
    """The text names no run, or more than one."""


def _passed(run: CompletedRunView) -> bool:
    return run.returncode == 0 and not (run.failed or 0) and not (run.broken or 0)


def resolve_run(ref: str, *, cycle: str | None = None) -> CompletedRunView:
    """The run *ref* names (an id, a unique id prefix or ``latest``)."""
    from qasight.history.read_model import get_run, latest_run_per_cycle, list_recent_runs

    text = (ref or "").strip()
    if not text:
        raise RunRefError("no run given")
    if text.lower() == "latest":
        latest = latest_run_per_cycle()
        if cycle:
            if cycle not in latest:
                raise RunRefError(f"cycle {cycle!r} has no recorded runs")
            return latest[cycle]
        if not latest:
            raise RunRefError("no runs are recorded yet")
        return next(iter(latest.values()))
    exact = get_run(run_id=text)
    if exact is not None:
        return exact
    matches = [r for r in list_recent_runs(limit=SEARCH_LIMIT) if r.run_id.startswith(text)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RunRefError(f"no recorded run {text!r} (see `qasight runs`)")
    raise RunRefError(f"{text!r} matches {len(matches)} runs; give more of the id")


def resolve_baseline(current: CompletedRunView, against: str | None) -> CompletedRunView:
    """The run to compare *current* with: ``previous`` by default."""
    from qasight.history.read_model import list_cycle_runs

    choice = (against or "previous").strip()
    lowered = choice.lower()
    earlier = (
        list_cycle_runs(current.cycle, limit=SEARCH_LIMIT, before_run_id=current.run_id)
        if current.cycle
        else []
    )
    if lowered == "previous":
        same_branch = [
            r for r in earlier if not current.branch or r.branch in (current.branch, None)
        ]
        if same_branch:
            return same_branch[0]
        if earlier:
            return earlier[0]
        raise RunRefError(
            f"run {current.run_id[:8]} is the first of cycle {current.cycle!r}; nothing to compare"
        )
    if lowered in {"last-green", "green", "last-pass"}:
        green = next((r for r in earlier if _passed(r)), None)
        if green is None:
            raise RunRefError(f"no earlier passing run of cycle {current.cycle!r}")
        return green
    on_branch = [r for r in earlier if r.branch == choice]
    if on_branch:
        return on_branch[0]
    try:
        return resolve_run(choice)
    except RunRefError:
        raise RunRefError(
            f"--against {choice!r} is not previous, last-green, a branch with earlier runs "
            "or a run id"
        ) from None
