"""``qasight users`` — accounts for a team server with ``TESTO_AUTH=accounts``.

Talks to the run-history database directly (``DATABASE_URL``, the same one the
server uses), so run it where the server's database is reachable, e.g.
``docker compose exec server qasight users add alice --role runner``.
"""

from __future__ import annotations

import sys

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(
    help="Manage team server accounts (used when the server runs with TESTO_AUTH=accounts).",
    no_args_is_help=True,
)

_ROLE_HELP = "viewer (read), runner (also start runs and upload), or admin (also settings)."


def _store():  # noqa: ANN202 - lazy import keeps `qasight --help` fast
    from qasight.accounts import AccountStore

    return AccountStore()


def _fail(message: str, code: EngineExitCode = EngineExitCode.INVALID_INPUT) -> typer.Exit:
    from qasight.cli.ui.console import default_console

    default_console().print(f"[fail]{message}[/]")
    return typer.Exit(code=int(code))


def _password(password_stdin: bool) -> str:
    if password_stdin:
        return sys.stdin.readline().rstrip("\n")
    return str(typer.prompt("Password", hide_input=True, confirmation_prompt=True))


@app.command("add")
def add(
    username: str = typer.Argument(..., help="Login name: letters, digits and . _ @ -"),
    role: str = typer.Option("viewer", "--role", "-r", help=_ROLE_HELP),
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help="Read the password from stdin instead of prompting."
    ),
) -> None:
    """Create an account."""
    from qasight.accounts import AccountError, check_new_account
    from qasight.cli.ui.console import default_console

    store = _store()
    try:
        # Name, role and "already exists" first, so nobody types a password twice for nothing.
        name = check_new_account(username, role)
        if store.get(name) is not None:
            raise AccountError(f"user {name!r} already exists")
        account = store.add(name, _password(password_stdin), role)
    except AccountError as exc:
        raise _fail(str(exc)) from exc
    default_console().print(f"[pass]added[/] {account.username} ({account.role})")


@app.command("list")
def list_users() -> None:
    """List accounts and their roles."""
    from rich.table import Table

    from qasight.cli.ui.console import default_console

    accounts = _store().all()
    console = default_console()
    if not accounts:
        console.print(
            "[muted]No accounts yet. Add one with `qasight users add NAME --role admin`.[/]"
        )
        return
    table = Table(title="Accounts", title_justify="left")
    table.add_column("username", style="bold")
    table.add_column("role")
    table.add_column("created", style="muted")
    for account in accounts:
        table.add_row(account.username, account.role, f"{account.created_at:%Y-%m-%d}")
    console.print(table)


@app.command("set-role")
def set_role(
    username: str = typer.Argument(...),
    role: str = typer.Argument(..., help=_ROLE_HELP),
) -> None:
    """Change an account's role (signs it out everywhere)."""
    from qasight.accounts import AccountError
    from qasight.cli.ui.console import default_console

    try:
        account = _store().set_role(username, role)
    except AccountError as exc:
        raise _fail(str(exc)) from exc
    default_console().print(f"[pass]{account.username}[/] is now {account.role}")


@app.command("reset-password")
def reset_password(
    username: str = typer.Argument(...),
    password_stdin: bool = typer.Option(
        False, "--password-stdin", help="Read the password from stdin instead of prompting."
    ),
) -> None:
    """Set a new password (signs the account out everywhere)."""
    from qasight.accounts import AccountError
    from qasight.cli.ui.console import default_console

    store = _store()
    try:
        if store.get(username.strip()) is None:
            raise AccountError(f"no user {username.strip()!r}")
        store.reset_password(username, _password(password_stdin))
    except AccountError as exc:
        raise _fail(str(exc)) from exc
    default_console().print(f"[pass]password changed[/] for {username}")


@app.command("sign-out")
def sign_out(username: str = typer.Argument(...)) -> None:
    """Sign an account out of every browser."""
    from qasight.accounts import AccountError
    from qasight.cli.ui.console import default_console

    try:
        _store().sign_out_everywhere(username)
    except AccountError as exc:
        raise _fail(str(exc)) from exc
    default_console().print(f"[pass]signed out[/] {username} everywhere")


@app.command("remove")
def remove(username: str = typer.Argument(...)) -> None:
    """Delete an account (its audit log lines stay)."""
    from qasight.accounts import AccountError
    from qasight.cli.ui.console import default_console

    try:
        _store().remove(username)
    except AccountError as exc:
        raise _fail(str(exc)) from exc
    from qasight.project_roles import ProjectRoleStore

    ProjectRoleStore().remove_user(username.strip())
    default_console().print(f"[pass]removed[/] {username}")


@app.command("grant")
def grant(
    username: str = typer.Argument(...),
    role: str = typer.Argument(..., help=_ROLE_HELP),
    pattern: str = typer.Argument(..., help="Cycle name or glob: payments, 'shop-*'."),
) -> None:
    """Give an account a role on the cycles matching a pattern (above or below its own role)."""
    from qasight.cli.ui.console import default_console
    from qasight.project_roles import ProjectRoleError, ProjectRoleStore

    if _store().get(username.strip()) is None:
        raise _fail(f"no user {username.strip()!r}")
    try:
        ProjectRoleStore().grant(
            username=username.strip(), pattern=pattern, role=role, granted_by="cli"
        )
    except ProjectRoleError as exc:
        raise _fail(str(exc)) from exc
    default_console().print(f"[pass]{username}[/] is {role} on cycles matching {pattern!r}")


@app.command("revoke")
def revoke(
    username: str = typer.Argument(...),
    pattern: str = typer.Argument(..., help="The pattern given to `qasight users grant`."),
) -> None:
    """Take back a per-cycle role; the account's own role applies there again."""
    from qasight.cli.ui.console import default_console
    from qasight.project_roles import ProjectRoleStore

    if not ProjectRoleStore().revoke(username=username.strip(), pattern=pattern):
        raise _fail(f"{username} has no role on {pattern!r}")
    default_console().print(f"[pass]revoked[/] {username} on {pattern!r}")


@app.command("grants")
def grants(username: str | None = typer.Argument(None)) -> None:
    """List per-cycle roles (one account's, or everyone's)."""
    from rich.table import Table

    from qasight.cli.ui.console import default_console
    from qasight.project_roles import ProjectRoleStore

    rows = ProjectRoleStore().list(username.strip() if username else None)
    console = default_console()
    if not rows:
        console.print("No per-cycle roles; every account has its own role on every cycle.")
        return
    table = Table("user", "cycles", "role")
    for row in rows:
        table.add_row(row.username, row.pattern, row.role)
    console.print(table)
