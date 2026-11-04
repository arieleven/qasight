"""``qasight lease`` — take a key from a resource pool, so parallel workers never share one.

Pools come from ``resources:`` in ``qasight.yaml``. Leases live on the team
server when ``TESTO_SERVER_URL`` (or ``--server``) is set, so every laptop and
CI agent shares one pool; otherwise in this machine's run-history database.
See :mod:`qasight.resources`.

``qasight lease run POOL -- CMD`` is the one to reach for: it waits for a key,
runs ``CMD`` with ``TESTO_RESOURCE_ID`` (and friends) set, keeps the lease alive
while it runs, and gives the key back when it ends, even if it fails. A process
that dies without giving it back loses the key after the pool's ``lease_ttl``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from qasight.engine.exit_codes import EngineExitCode

if TYPE_CHECKING:
    from qasight.resources.client import LeaseBackend
    from qasight.resources.pools import ResourcePool
    from qasight.resources.store import TakeResult

app = typer.Typer(
    help="Take keys from a resource pool (resources: in qasight.yaml) for parallel workers.",
    no_args_is_help=True,
)

_SERVER = typer.Option(
    None,
    "--server",
    help="QASight server that keeps the leases (default: $TESTO_SERVER_URL; "
    "none means this machine's database). The token comes from $TESTO_API_TOKEN.",
)
_CONFIG = typer.Option(
    None, "--config", help="qasight.yaml with the 'resources:' pools (default: auto-discover)."
)
_LABELS = typer.Option(
    None, "--label", "-l", help="Only keys with this label (repeat for several)."
)
_WAIT = typer.Option(
    "30m",
    "--wait",
    help="How long to wait for a free key: 90s, 10m, 2h, 0 (don't wait) or forever.",
)


def parse_wait(raw: str) -> float | None:
    """``--wait`` in seconds; None waits forever."""
    from qasight.resources.pools import parse_duration

    try:
        return parse_duration(raw)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--wait") from exc


def _console(*, stderr: bool = False) -> Any:
    from rich.console import Console

    from qasight.cli.ui.console import _THEME, default_console

    if not stderr:
        return default_console()
    return Console(theme=_THEME, stderr=True, soft_wrap=True, highlight=False)


def _fail(message: str, code: EngineExitCode) -> typer.Exit:
    from rich.markup import escape

    _console(stderr=True).print(f"[fail]{escape(message)}[/]")
    return typer.Exit(code=int(code))


def _pool(name: str, config: Path | None) -> ResourcePool:
    from qasight.config.errors import ConfigError
    from qasight.resources.client import find_pool

    try:
        return find_pool(name, config_path=config)
    except ConfigError as exc:
        raise _fail(str(exc), EngineExitCode.INVALID_INPUT) from exc


def _backend(server: str | None) -> LeaseBackend:
    import os

    from qasight.cli.server_url import server_url_problem
    from qasight.resources.client import SERVER_URL_ENV, backend_from_env

    problem = server_url_problem(server or os.environ.get(SERVER_URL_ENV))
    if problem:
        raise _fail(problem, EngineExitCode.INVALID_INPUT)
    return backend_from_env(server=server)


def _waiting_printer() -> Any:
    console = _console(stderr=True)
    last: list[tuple[int, int, str | None]] = []

    def on_wait(result: TakeResult) -> None:
        state = (result.ahead, result.free, result.reason)
        if last and last[-1] == state:
            return
        last.append(state)
        if result.reason == "limit":
            console.print(
                "[muted]qasight lease: you hold as many keys as this pool allows; waiting[/]"
            )
        else:
            console.print(
                f"[muted]qasight lease: waiting for a key ({result.ahead} ahead, "
                f"{result.free} free)[/]"
            )

    return on_wait


def _acquire(
    pool: ResourcePool,
    *,
    labels: list[str] | None,
    wait: str,
    server: str | None,
    note: str,
    ttl: float | None = None,
    keep_alive: bool = True,
) -> Any:
    from dataclasses import replace

    from qasight.resources.client import LeaseTimeout, acquire, default_holder
    from qasight.resources.store import LeaseError

    if ttl is not None:
        pool = replace(pool, lease_ttl_s=ttl)
    try:
        return acquire(
            _backend(server),
            pool,
            labels=labels or (),
            holder=default_holder(note=note),
            wait_s=parse_wait(wait),
            on_wait=_waiting_printer(),
            keep_alive=keep_alive,
        )
    except (LeaseTimeout, LeaseError) as exc:
        raise _fail(str(exc), EngineExitCode.INFRA_FAILURE) from exc


@app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run(
    ctx: typer.Context,
    pool_name: str = typer.Argument(..., metavar="POOL", help="Pool name from 'resources:'."),
    labels: list[str] | None = _LABELS,
    wait: str = _WAIT,
    note: str = typer.Option(
        "", "--note", help="Shown next to the holder in `qasight lease list`."
    ),
    server: str | None = _SERVER,
    config: Path | None = _CONFIG,
) -> None:
    """Hold a key while a command runs: qasight lease run POOL -- CMD [ARGS...]. Exits with CMD's code."""
    import os
    import signal
    import subprocess

    command = list(ctx.args)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise _fail(
            "give the command to run after --, e.g. qasight lease run vms -- pytest",
            EngineExitCode.INVALID_INPUT,
        )
    pool = _pool(pool_name, config)
    held = _acquire(pool, labels=labels, wait=wait, server=server, note=note)
    proc: subprocess.Popen[bytes] | None = None

    def stop(signum: int, _frame: object) -> None:
        if proc is not None and proc.poll() is None:
            proc.send_signal(signum)

    previous = signal.signal(signal.SIGTERM, stop)
    _console(stderr=True).print(f"[muted]qasight lease: holding {pool.name}/{held.id}[/]")
    try:
        try:
            proc = subprocess.Popen(command, env={**os.environ, **held.env()})
        except OSError as exc:
            raise _fail(
                f"could not start {command[0]}: {exc}", EngineExitCode.INVALID_INPUT
            ) from exc
        try:
            returncode = proc.wait()
        except KeyboardInterrupt:
            returncode = proc.wait()
    finally:
        signal.signal(signal.SIGTERM, previous)
        held.release()
    if held.lost.is_set():
        _console(stderr=True).print(
            f"[warn]qasight lease: the lease on {held.id} was lost while the command ran "
            "(it expired or an admin took it back)[/]"
        )
    raise typer.Exit(code=returncode if returncode >= 0 else 128 - returncode)


@app.command("take")
def take(
    pool_name: str = typer.Argument(..., metavar="POOL"),
    labels: list[str] | None = _LABELS,
    wait: str = _WAIT,
    ttl: str | None = typer.Option(
        None,
        "--ttl",
        help="Hold it this long without heartbeats (default: the pool's lease_ttl), e.g. 2h.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the lease as JSON."),
    note: str = typer.Option("", "--note"),
    server: str | None = _SERVER,
    config: Path | None = _CONFIG,
) -> None:
    """Take a key and print it with its lease id. Keep it with `heartbeat`, give it back with `release`."""
    import json

    pool = _pool(pool_name, config)
    ttl_s = parse_wait(ttl) if ttl else None
    # This process exits now; the caller keeps the lease alive with `heartbeat`.
    held = _acquire(
        pool, labels=labels, wait=wait, server=server, note=note, ttl=ttl_s, keep_alive=False
    )
    lease = held.lease
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "key": lease.item,
                    "pool": lease.pool,
                    "lease_id": lease.lease_id,
                    "expires_at": lease.expires_at,
                    "env": held.env(),
                }
            )
        )
    else:
        typer.echo(f"{lease.item} {lease.lease_id}")


@app.command("heartbeat")
def heartbeat(
    lease_id: str = typer.Argument(...),
    server: str | None = _SERVER,
) -> None:
    """Keep a lease from `take` alive for another lease_ttl. Exits 1 when it is gone."""
    from qasight.resources.store import LeaseError

    try:
        lease = _backend(server).heartbeat(lease_id)
    except LeaseError as exc:
        raise _fail(str(exc), EngineExitCode.INFRA_FAILURE) from exc
    if lease is None:
        raise _fail("that lease is gone (expired or released)", EngineExitCode.DOMAIN_FAILURE)


@app.command("release")
def release(
    lease_id: str = typer.Argument(...),
    clean: bool = typer.Option(
        False,
        "--clean",
        help="You reset the key yourself: the next taker gets TESTO_RESOURCE_CLEAN=1.",
    ),
    server: str | None = _SERVER,
) -> None:
    """Give back a key from `take`."""
    from qasight.resources.store import LeaseError

    try:
        _backend(server).release(lease_id, clean=clean)
    except LeaseError as exc:
        raise _fail(str(exc), EngineExitCode.INFRA_FAILURE) from exc


@app.command("list")
def list_pools(
    pool_name: str | None = typer.Argument(None, metavar="[POOL]"),
    as_json: bool = typer.Option(False, "--json", help="Print the pools as JSON."),
    server: str | None = _SERVER,
    config: Path | None = _CONFIG,
) -> None:
    """Show each pool's keys: who holds them, since when, and how many requests wait."""
    import json
    import time
    from dataclasses import asdict

    from rich.markup import escape
    from rich.table import Table

    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load
    from qasight.resources.store import LeaseError

    try:
        configured = list(discover_and_load(config_path=config).resources.values())
    except ConfigError:
        configured = []
    if pool_name:
        configured = [p for p in configured if p.name == pool_name]
    try:
        pools = _backend(server).status(pool_name, pools=configured)
    except LeaseError as exc:
        raise _fail(str(exc), EngineExitCode.INFRA_FAILURE) from exc
    if as_json:
        typer.echo(json.dumps([asdict(p) for p in pools], default=str))
        return
    console = _console()
    if not pools:
        console.print("[muted]No pools yet. Add one under 'resources:' in qasight.yaml.[/]")
        return
    now = time.time()
    for pool in pools:
        table = Table(
            title=f"{pool.name}: {pool.free} free of {len(pool.items)}, {pool.waiting} waiting",
            title_justify="left",
        )
        table.add_column("key", style="bold")
        table.add_column("state")
        table.add_column("holder")
        table.add_column("since", style="muted")
        for item in pool.items:
            if item.disabled:
                state, holder, since = "[fail]disabled[/]", escape(item.disabled_reason), ""
            elif item.lease:
                h = item.lease.holder
                where = "/".join(x for x in (h.run_id, h.stage, h.worker) if x)
                holder = escape(
                    " ".join(
                        x for x in (h.user, f"@{h.host}" if h.host else "", where, h.note) if x
                    )
                )
                state, since = "[warn]held[/]", f"{int(now - item.lease.taken_at)}s ago"
                if item.lease.expired(now):
                    free_in = item.lease.expires_at + item.lease.reuse_delay_s - now
                    state = f"[warn]expired, free in {max(0, int(free_in))}s[/]"
            else:
                state, holder, since = "[ok]free[/]" + (" (clean)" if item.clean else ""), "", ""
            table.add_row(escape(item.item), state, holder, since)
        console.print(table)


def _admin(action: str, pool: str, key: str, server: str | None, reason: str = "") -> None:
    from qasight.resources.store import LeaseError

    backend = _backend(server)
    try:
        if action == "free":
            done = backend.force_release(pool, key)
        else:
            done = backend.set_disabled(pool, key, disabled=action == "disable", reason=reason)
    except LeaseError as exc:
        raise _fail(str(exc), EngineExitCode.INFRA_FAILURE) from exc
    if not done:
        raise _fail(f"{pool}/{key}: nothing to {action}", EngineExitCode.DOMAIN_FAILURE)
    _console().print(
        f"[ok]{action}d[/] {pool}/{key}" if action != "free" else f"[ok]freed[/] {pool}/{key}"
    )


@app.command("disable")
def disable(
    pool_name: str = typer.Argument(..., metavar="POOL"),
    key: str = typer.Argument(...),
    reason: str = typer.Option("", "--reason", help="Why (shown in the list and the UI)."),
    server: str | None = _SERVER,
) -> None:
    """Take a broken key out of its pool until `enable`."""
    _admin("disable", pool_name, key, server, reason)


@app.command("enable")
def enable(
    pool_name: str = typer.Argument(..., metavar="POOL"),
    key: str = typer.Argument(...),
    server: str | None = _SERVER,
) -> None:
    """Put a disabled key back in its pool."""
    _admin("enable", pool_name, key, server)


@app.command("free")
def free(
    pool_name: str = typer.Argument(..., metavar="POOL"),
    key: str = typer.Argument(...),
    server: str | None = _SERVER,
) -> None:
    """Take a key back from whoever holds it (an admin on a team server)."""
    _admin("free", pool_name, key, server)


@app.command("demo")
def demo(
    clear: bool = typer.Option(False, "--clear", help="Give the demo keys back instead."),
) -> None:
    """Fill this machine's pools with made-up keys and holders, to try the Resources page.

    Adds pools demo-flow-vms, demo-phones and demo-license-seats with some keys held by
    invented people, one disabled and two requests waiting (those leave after about 30 s).
    """
    import os

    from qasight.cli.ui.console import default_console
    from qasight.resources.client import LEASE_DB_ENV, backend_from_env
    from qasight.resources.demo import clear_demo, seed_demo
    from qasight.resources.store import LeaseStore

    # Always this machine's database (the one `qasight ui` shows), never a team server.
    env = {k: v for k, v in os.environ.items() if k == LEASE_DB_ENV}
    store = backend_from_env(env=env)
    assert isinstance(store, LeaseStore)
    console = default_console()
    if clear:
        freed = clear_demo(store)
        console.print(f"[ok]Gave back {freed} demo key(s).[/]")
        return
    result = seed_demo(store)
    console.print(
        f"[ok]Demo pools ready:[/] {result.held} keys held, {result.disabled} disabled, "
        f"{result.waiting} waiting. See them with `qasight lease list` or on the Resources "
        "page of `qasight ui`. Undo with `qasight lease demo --clear`."
    )
