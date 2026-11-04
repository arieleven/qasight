"""``qasight pool`` — look at and change the real keys behind a resource pool, with your own commands.

QASight never creates or deletes a VM (or any key) on its own. A pool's
``provider:`` holds the config author's commands, and only this command group
runs them, when a person types it.

- ``status`` is read-only: it runs ``provider.list`` and, with ``--check``, the
  pool's ``on_check`` on each free key, then says which keys are missing, which
  exist outside the pool, and which fail.
- ``add``, ``remove`` and ``sweep`` run your ``create`` / ``destroy`` commands. They
  print what they would do unless ``--apply``, ask before running unless ``--yes``,
  and log every command they run (``qasight pool log``, the Resources page).

See :mod:`qasight.resources.provider`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from qasight.cli.commands.lease import _CONFIG, _SERVER, _backend, _console, _fail
from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(
    help="Compare resource pools with the keys that really exist, and add or remove them "
    "with your own provider commands (never on its own).",
    no_args_is_help=True,
)


@app.command("status")
def status(
    pool_name: str | None = typer.Argument(None, metavar="[POOL]"),
    check: bool = typer.Option(
        False, "--check", help="Also run each pool's on_check on every free key."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the reports as JSON."),
    server: str | None = _SERVER,
    config: Path | None = _CONFIG,
) -> None:
    """Missing keys, leftovers and failed checks. Exits 1 when any pool has one, 3 when a list command fails."""
    import json
    from dataclasses import asdict

    from rich.markup import escape
    from rich.table import Table

    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.resources.provider import pool_report
    from qasight.resources.store import LeaseError

    try:
        pools = list(discover_and_load(config_path=config).resources.values())
    except ConfigError as exc:
        raise _fail(str(exc), EngineExitCode.INVALID_INPUT) from exc
    if pool_name:
        pools = [p for p in pools if p.name == pool_name]
        if not pools:
            raise _fail(
                f"no pool {pool_name!r} under 'resources:' in the config",
                EngineExitCode.INVALID_INPUT,
            )
    try:
        leases = {s.name: s.items for s in _backend(server).status(pool_name, pools=pools)}
    except LeaseError as exc:
        raise _fail(str(exc), EngineExitCode.INFRA_FAILURE) from exc

    note = _console(stderr=True)
    reports = []
    for pool in pools:
        if pool.provider and pool.provider.list:
            note.print(f"[muted]qasight pool: {pool.name}: running {escape(pool.provider.list)}[/]")
        if check and pool.on_check:
            note.print(f"[muted]qasight pool: {pool.name}: on_check on each free key[/]")
        reports.append(pool_report(pool, leases.get(pool.name, ()), check=check))

    if as_json:
        typer.echo(
            json.dumps(
                [{**asdict(r), "missing": list(r.missing), "healthy": r.healthy} for r in reports]
            )
        )
    else:
        console = _console()
        if not reports:
            console.print("[muted]No pools yet. Add one under 'resources:' in qasight.yaml.[/]")
        for report in reports:
            if report.list_problem:
                verdict = f"[fail]{escape(report.list_problem)}[/]"
            elif report.healthy:
                verdict = "[ok]matches[/]" if report.has_list else "[muted]no provider.list[/]"
            else:
                verdict = "[warn]needs a look[/]"
            table = Table(title=f"{report.pool}: {verdict}", title_justify="left")
            table.add_column("key", style="bold")
            table.add_column("exists")
            table.add_column("lease")
            table.add_column("check")
            for key in report.keys:
                exists = {True: "[ok]yes[/]", False: "[fail]missing[/]", None: "[muted]?[/]"}
                checked = (
                    ""
                    if key.check is None
                    else "[ok]ok[/]"
                    if key.check == ""
                    else f"[muted]{escape(key.check)}[/]"
                    if key.check_skipped
                    else f"[fail]{escape(key.check)}[/]"
                )
                table.add_row(escape(key.key), exists[key.exists], key.state, checked)
            for extra in report.extra:
                table.add_row(escape(extra), "[warn]not in pool[/]", "", "")
            console.print(table)
        console.print("[muted]Read-only: status never creates or deletes anything.[/]")

    if any(r.list_problem for r in reports):
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE))
    if not all(r.healthy for r in reports):
        raise typer.Exit(code=int(EngineExitCode.DOMAIN_FAILURE))


_APPLY = typer.Option(
    False, "--apply", help="Run the commands. Without it, only print what would run."
)
_YES = typer.Option(False, "--yes", "-y", help="With --apply: don't ask before running.")


def _pool_and_listing(pool_name: str, config: Path | None) -> tuple[Any, Any]:
    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.resources.provider import list_keys

    try:
        pools = discover_and_load(config_path=config).resources
    except ConfigError as exc:
        raise _fail(str(exc), EngineExitCode.INVALID_INPUT) from exc
    if pool_name not in pools:
        raise _fail(
            f"no pool {pool_name!r} under 'resources:' in the config", EngineExitCode.INVALID_INPUT
        )
    pool = pools[pool_name]
    if pool.provider and pool.provider.list:
        _console(stderr=True).print(
            f"[muted]qasight pool: {pool.name}: running {_escape(pool.provider.list)}[/]"
        )
    listing = list_keys(pool)
    if listing.problem:
        # Never guess which keys exist before creating or deleting any.
        raise _fail(f"{listing.problem}; nothing ran.", EngineExitCode.INFRA_FAILURE)
    return pool, listing


def _escape(text: str) -> str:
    from rich.markup import escape

    return escape(text)


def _leases(pool: Any, server: str | None) -> Any:
    from qasight.resources.store import LeaseError

    try:
        found = _backend(server).status(pool.name, pools=[pool])
    except LeaseError as exc:
        raise _fail(str(exc), EngineExitCode.INFRA_FAILURE) from exc
    return next((s.items for s in found if s.name == pool.name), ())


def _carry_out(plan: Any, pool: Any, *, apply: bool, yes: bool, server: str | None) -> None:
    """Print the plan; with ``--apply`` run it, logging each command, and exit by how it went."""
    from qasight.resources.client import default_holder
    from qasight.resources.provider import run_step
    from qasight.resources.store import LeaseError, ProviderAction

    console = _console()
    for key, why in plan.refused:
        console.print(f"[warn]skip[/] {_escape(key)}: {_escape(why)}")
    if plan.note:
        console.print(f"[muted]{_escape(plan.note)}[/]")
    if not plan.steps:
        console.print("[muted]Nothing to run.[/]")
        raise typer.Exit(code=int(EngineExitCode.DOMAIN_FAILURE if plan.refused else 0))
    for step in plan.steps:
        console.print(
            f"{'run' if apply else 'would run'} [bold]{step.verb}[/] for {_escape(step.key)}: "
            f"{_escape(step.command)}  [muted](TESTO_RESOURCE_ID={_escape(step.key)})[/]"
        )
    if not apply:
        console.print("[muted]Dry run: nothing ran. Add --apply to run these commands.[/]")
        return
    if not yes:
        typer.confirm(f"Run {len(plan.steps)} {plan.steps[0].verb} command(s)?", abort=True)

    backend = _backend(server)
    me = default_holder()
    user, host = me.user, me.host
    failed = 0
    made: list[str] = []
    for step in plan.steps:
        problem, output = run_step(pool, step)
        ok = problem is None
        failed += not ok
        action = ProviderAction(
            pool=pool.name,
            item=step.key,
            verb=step.verb,
            user=user,
            host=host,
            command=step.command,
            ok=ok,
            detail=problem or output,
        )
        try:
            backend.record_action(action)
        except LeaseError as exc:
            # The log is part of the deal: stop before running anything else.
            raise _fail(
                f"{step.verb} {step.key} {'worked' if ok else 'failed'}, but logging it failed: "
                f"{exc}. Stopped.",
                EngineExitCode.INFRA_FAILURE,
            ) from exc
        if ok and step.verb == "create":
            made.append(step.key)
        mark = "[ok]done[/]" if ok else f"[fail]failed: {_escape(problem or '')}[/]"
        console.print(f"{step.verb} {_escape(step.key)}: {mark}")
    if made:
        console.print(
            f"[muted]Add {_escape(', '.join(made))} to resources.{pool.name}.items in "
            "qasight.yaml to use them: QASight doesn't change your config.[/]"
        )
    if failed:
        raise typer.Exit(code=int(EngineExitCode.DOMAIN_FAILURE))


def _refused(exc: Exception) -> typer.Exit:
    return _fail(str(exc), EngineExitCode.INVALID_INPUT)


@app.command("add")
def add(
    pool_name: str = typer.Argument(..., metavar="POOL"),
    names: list[str] | None = typer.Argument(None, metavar="[KEY...]"),
    count: int = typer.Option(
        0, "--count", "-n", min=0, help="Make N keys named <name_prefix><n>."
    ),
    apply: bool = _APPLY,
    yes: bool = _YES,
    server: str | None = _SERVER,
    config: Path | None = _CONFIG,
) -> None:
    """Make new keys with your provider.create command. A dry run unless --apply.

    The new keys are not in the pool until you add them to its items in qasight.yaml.
    """
    from qasight.resources.provider import ProviderRefused, plan_add

    pool, listing = _pool_and_listing(pool_name, config)
    try:
        plan = plan_add(pool, listing, names=names or (), count=count)
    except ProviderRefused as exc:
        raise _refused(exc) from exc
    if not plan.steps and not plan.refused:
        raise _fail("give key names or --count", EngineExitCode.INVALID_INPUT)
    _carry_out(plan, pool, apply=apply, yes=yes, server=server)


@app.command("remove")
def remove(
    pool_name: str = typer.Argument(..., metavar="POOL"),
    names: list[str] = typer.Argument(..., metavar="KEY..."),
    apply: bool = _APPLY,
    yes: bool = _YES,
    server: str | None = _SERVER,
    config: Path | None = _CONFIG,
) -> None:
    """Remove keys with your provider.destroy command. A dry run unless --apply.

    Refuses keys still in the pool's items, keys someone holds and names without name_prefix.
    """
    from qasight.resources.provider import ProviderRefused, plan_remove

    pool, listing = _pool_and_listing(pool_name, config)
    try:
        plan = plan_remove(pool, listing, _leases(pool, server), names)
    except ProviderRefused as exc:
        raise _refused(exc) from exc
    _carry_out(plan, pool, apply=apply, yes=yes, server=server)


@app.command("sweep")
def sweep(
    pool_name: str = typer.Argument(..., metavar="POOL"),
    apply: bool = _APPLY,
    yes: bool = _YES,
    server: str | None = _SERVER,
    config: Path | None = _CONFIG,
) -> None:
    """Remove leftovers: keys provider.list shows with name_prefix that aren't in the pool or held."""
    from qasight.resources.provider import ProviderRefused, plan_sweep

    pool, listing = _pool_and_listing(pool_name, config)
    try:
        plan = plan_sweep(pool, listing, _leases(pool, server))
    except ProviderRefused as exc:
        raise _refused(exc) from exc
    _carry_out(plan, pool, apply=apply, yes=yes, server=server)


@app.command("log")
def log(
    pool_name: str | None = typer.Argument(None, metavar="[POOL]"),
    limit: int = typer.Option(20, "--limit", min=1, max=500, help="How many, newest first."),
    as_json: bool = typer.Option(False, "--json", help="Print the log as JSON."),
    server: str | None = _SERVER,
) -> None:
    """Every create and destroy command people ran with --apply: who, where, when, how it went."""
    import json
    from dataclasses import asdict
    from datetime import datetime

    from rich.table import Table

    from qasight.resources.store import LeaseError

    try:
        actions = _backend(server).actions(pool_name, limit=limit)
    except LeaseError as exc:
        raise _fail(str(exc), EngineExitCode.INFRA_FAILURE) from exc
    if as_json:
        typer.echo(json.dumps([asdict(a) for a in actions]))
        return
    console = _console()
    if not actions:
        console.print("[muted]No provider commands run yet.[/]")
        return
    table = Table(title="Provider commands, newest first", title_justify="left")
    for column in ("when", "pool", "key", "verb", "who", "result"):
        table.add_column(column)
    for a in actions:
        result = "[ok]ok[/]" if a.ok else f"[fail]{_escape(a.detail or 'failed')}[/]"
        table.add_row(
            datetime.fromtimestamp(a.at).strftime("%Y-%m-%d %H:%M"),
            _escape(a.pool),
            _escape(a.item),
            a.verb,
            _escape(f"{a.user}@{a.host}" if a.host else a.user),
            result,
        )
    console.print(table)
