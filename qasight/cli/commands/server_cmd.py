"""``qasight server init`` — the files to run a team server (:mod:`qasight.services.server_setup`)."""

from __future__ import annotations

from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(no_args_is_help=True)


@app.command("init")
def init(
    directory: Path = typer.Option(
        Path("qasight-server"), "--dir", help="Folder to write into (created)."
    ),
    domain: str = typer.Option(
        None,
        "--domain",
        help="Serve https://DOMAIN with Caddy, which gets the certificate itself "
        "(ports 80/443 open, DNS pointing at this machine).",
    ),
    admin: str = typer.Option(
        None,
        "--admin",
        help="Turn on accounts (TESTO_AUTH=accounts) with this first admin; its generated "
        "password is in .env.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite files (a new token too)."),
) -> None:
    """Write docker-compose.yml, a .env with a generated token, and optionally HTTPS and accounts."""
    from qasight.services.server_setup import ServerSetupError, write_server_files

    folder = directory.expanduser().resolve()
    try:
        result = write_server_files(folder, domain=domain, admin=admin, force=force)
    except ServerSetupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT)) from None
    for name in result.written:
        typer.echo(f"Wrote {folder / name}" + ("  (secrets, mode 600)" if name == ".env" else ""))
    typer.echo("\nNext:")
    typer.echo(f"  cd {folder} && docker compose up -d")
    typer.echo(
        f"  open {result.url}" + (" and sign in with the token in .env" if not result.admin else "")
    )
    if result.admin:
        typer.echo(f"  sign in as {result.admin} with TESTO_ADMIN_PASSWORD from .env")
    typer.echo(
        "\nIn each project, so CI uploads every run (TESTO_API_TOKEN from .env as $TESTO_API_TOKEN):"
    )
    typer.echo(f"  TESTO_API_TOKEN=... qasight init --server {result.url}")
    typer.echo(f"  or: qasight ingest 'reports/**/*.xml' --cycle unit --server {result.url}")
    typer.echo(
        "Backups: a copy of the history daily in the testo_data volume (see the compose file)."
    )
