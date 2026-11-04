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
    default_console().print(f"[pass]removed[/] {username}")
