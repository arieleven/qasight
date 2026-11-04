"""``qasight quarantine`` — the quarantine list kept in the run history (no config change).

The same list as the UI's "Quarantine" button and ``/api/v1/quarantine``
(:mod:`qasight.services.quarantine_store`), on this machine's run history,
or on a team server with ``--server`` (or ``TESTO_SERVER_URL``). Runs of the
cycle add it to the ``quarantine:`` rules of ``qasight.yaml``.
"""

from __future__ import annotations

import json
import os
from typing import Any

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(
    help="Quarantine tests without a config change: their failures stop failing runs.",
    no_args_is_help=True,
)

_SERVER_HELP = (
    "Change the list on this QASight server instead of this machine's "
    "(default: $TESTO_SERVER_URL). The token comes from $TESTO_API_TOKEN."
)


def _server(server: str | None) -> str | None:
    return (server or os.environ.get("TESTO_SERVER_URL") or "").strip() or None


def _call(method: str, server: str, *, params: dict[str, str], body: Any = None) -> Any:
    """One request to the server's quarantine route; exits 3 when it fails."""
    import requests

    token = os.environ.get("TESTO_API_TOKEN") or ""
    url = f"{server.rstrip('/')}/api/v1/quarantine"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.request(method, url, params=params, json=body, headers=headers, timeout=30)
    except requests.RequestException as exc:
        typer.echo(f"could not reach {url}: {exc}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INFRA_FAILURE)) from exc
    if resp.status_code == 404 and method == "DELETE":
        return None
    if resp.status_code >= 400:
        from qasight.services.ingest_client import _error_message

        typer.echo(f"{url} answered {resp.status_code}: {_error_message(resp)}", err=True)
        code = (
            EngineExitCode.INVALID_INPUT if resp.status_code < 500 else EngineExitCode.INFRA_FAILURE
        )
        raise typer.Exit(code=int(code))
    return resp.json() if resp.content else {}


def _who() -> str:
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name (containers); the entry just names no one
        return ""


@app.command("list")
def list_cmd(
    cycle: str | None = typer.Option(None, "--cycle", help="Only this cycle's entries."),
    as_json: bool = typer.Option(False, "--json", help="Print the entries as JSON."),
    server: str | None = typer.Option(None, "--server", help=_SERVER_HELP),
) -> None:
    """List quarantined tests, newest first (not the ones in qasight.yaml)."""
    from qasight.services.quarantine_store import QuarantineStore, is_glob, release_after

    url = _server(server)
    if url:
        params = {"cycle": cycle.strip()} if cycle else {}
        data = _call("GET", url, params=params)
        items = list(data.get("items", []))
        after = int(data.get("release_after", 0))
    else:
        items = [
            {
                "cycle": e.cycle,
                "test": e.test,
                "is_glob": e.is_glob,
                "reason": e.reason,
                "owner": e.owner,
                "added_by": e.added_by,
                "added_at": e.added_at.isoformat(),
            }
            for e in QuarantineStore().list(cycle.strip() if cycle else None)
        ]
        after = release_after()
    if as_json:
        typer.echo(json.dumps({"items": items, "release_after": after}, indent=2))
        return
    if not items:
        typer.echo("No quarantined tests" + (f" in cycle {cycle}." if cycle else "."))
        return
    for item in items:
        extra = [
            f"reason: {item['reason']}" if item.get("reason") else "",
            f"owner: {item['owner']}" if item.get("owner") else "",
            f"added by {item['added_by']}" if item.get("added_by") else "",
        ]
        details = "; ".join(x for x in extra if x)
        typer.echo(f"{item['cycle']}  {item['test']}" + (f"  ({details})" if details else ""))
    if after:
        globs = any(item.get("is_glob", is_glob(str(item["test"]))) for item in items)
        note = " (globs stay until removed)" if globs else ""
        typer.echo(f"An exact name is released after {after} clean runs in a row{note}.")


@app.command("add")
def add_cmd(
    test: str = typer.Argument(
        ..., help="The test's name or full name as runs record it, or an fnmatch glob."
    ),
    cycle: str = typer.Option(..., "--cycle", help="Cycle whose runs the test stops failing."),
    reason: str | None = typer.Option(None, "--reason", help="Why, e.g. an issue link."),
    owner: str | None = typer.Option(None, "--owner", help="Who is fixing it."),
    server: str | None = typer.Option(None, "--server", help=_SERVER_HELP),
) -> None:
    """Quarantine a test: it still runs and is reported, but its failure no longer fails the run."""
    from qasight.services.quarantine_store import (
        QuarantineError,
        QuarantineStore,
        is_glob,
        record_audit,
        release_after,
    )

    url = _server(server)
    if url:
        body = {"cycle": cycle.strip(), "test": test.strip(), "reason": reason, "owner": owner}
        _call("PUT", url, params={}, body=body)
        where = f" on {url}"
    else:
        user = _who()
        try:
            QuarantineStore().add(
                cycle=cycle, test=test, reason=reason, owner=owner, added_by=user or None
            )
        except QuarantineError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc
        record_audit(username=user or "cli", method="PUT", cycle=cycle.strip(), test=test.strip())
        where = ""
    typer.echo(f"Quarantined {test.strip()} in cycle {cycle.strip()}{where}.")
    after = release_after()
    if not is_glob(test) and after and not url:
        typer.echo(f"It is released after {after} clean runs in a row.")
    if not url:
        hint = ci_hint(cycle=cycle.strip(), test=test.strip(), reason=reason)
        if hint:
            typer.echo(hint)


def ci_hint(*, cycle: str, test: str, reason: str | None) -> str | None:
    """When the list went to a local SQLite file, how to quarantine in CI too.

    CI (the GitHub Action's cached history, a fresh runner) never sees this
    machine's database, so a team that only ran ``qasight quarantine add`` would
    still see the test fail there. ``None`` for a shared database.
    """
    from qasight.repository.db_config import resolve_database_url

    try:
        db_url = resolve_database_url()
    except Exception:  # noqa: BLE001 - a hint is never worth an error
        return None
    if not db_url.startswith("sqlite"):
        return None
    lines = [
        "",
        "This list is in this machine's run history, which CI doesn't see. To quarantine it "
        "in CI too, add to the cycle in qasight.yaml:",
        "cycles:",
        f"  {json.dumps(cycle)}:",
        "    quarantine:",
        f"      - test: {json.dumps(test)}",
    ]
    if reason and reason.strip():
        lines.append(f"        reason: {json.dumps(reason.strip())}")
    lines.append("or use a team server (qasight quarantine add --server URL).")
    return "\n".join(lines)


@app.command("remove")
def remove_cmd(
    test: str = typer.Argument(..., help="The entry to remove, exactly as listed."),
    cycle: str = typer.Option(..., "--cycle", help="Cycle the entry belongs to."),
    server: str | None = typer.Option(None, "--server", help=_SERVER_HELP),
) -> None:
    """Release a test: its failures fail runs again. Exit 1 when it wasn't quarantined."""
    from qasight.services.quarantine_store import QuarantineStore, record_audit

    url = _server(server)
    params = {"cycle": cycle.strip(), "test": test.strip()}
    if url:
        removed = _call("DELETE", url, params=params) is not None
    else:
        removed = QuarantineStore().remove(cycle=cycle, test=test)
        if removed:
            record_audit(username=_who() or "cli", method="DELETE", **params)
    if not removed:
        typer.echo(f"{test.strip()} isn't quarantined in cycle {cycle.strip()}.", err=True)
        raise typer.Exit(code=int(EngineExitCode.DOMAIN_FAILURE))
    typer.echo(f"Released {test.strip()} in cycle {cycle.strip()}.")
