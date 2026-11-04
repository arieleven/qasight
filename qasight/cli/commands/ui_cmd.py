"""``qasight ui`` — open the web UI on this machine's run history.

Starts the API (``qasight_api``) on 127.0.0.1 with the UI that ships in the
wheel and opens a browser on it. Same history as the CLI: whatever
``DATABASE_URL`` / ``qasight.yaml`` point at, by default the SQLite file
next to the project. ``--from-github`` opens the history the GitHub Action
keeps instead (:mod:`qasight.services.github_history`).

The UI's writes (starting a run, saving AI settings) come from the server's
own origin, so that origin is added to ``TESTO_CORS_ORIGINS`` for this
process. That is the exact ``http://127.0.0.1:<port>`` and
``http://localhost:<port>``, nothing wider: a page on any other site, or one
that DNS-rebinds to 127.0.0.1, still has a foreign ``Origin`` and is refused.
"""

from __future__ import annotations

import os
import threading
import webbrowser

import typer

from qasight.engine.exit_codes import EngineExitCode

_BUILD_HINT = (
    "No built UI found. A `pip install qasight[api]` from PyPI includes one; from a "
    "source checkout run `scripts/build_ui_bundle.sh` (needs Node 22), or point "
    'TESTO_UI_DIR at a `npm run build` output made with VITE_API_BASE_URL="".'
)


def _own_origins(port: int) -> list[str]:
    return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]


def _merged_cors(port: int) -> str:
    from qasight_api.security import DEFAULT_CORS_ORIGINS

    current = [o.strip() for o in os.environ.get("TESTO_CORS_ORIGINS", "").split(",") if o.strip()]
    base = current or list(DEFAULT_CORS_ORIGINS)
    return ",".join(dict.fromkeys([*base, *_own_origins(port)]))


def ui(
    port: int = typer.Option(8000, "--port", "-p", min=1, max=65535, help="Port to serve on."),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the UI in the default browser."
    ),
    from_github: bool = typer.Option(
        False,
        "--from-github",
        help="Open the history the GitHub Action keeps (its newest testo-history artifact), "
        "via the gh CLI, instead of this machine's.",
    ),
    repo: str = typer.Option(
        None, "--repo", help="With --from-github: owner/name (default: this checkout's)."
    ),
    run_id: str = typer.Option(
        None, "--run-id", help="With --from-github: the history saved by this workflow run."
    ),
) -> None:
    """Serve the web UI and the API on http://127.0.0.1:PORT for this machine's run history."""
    if (repo or run_id) and not from_github:
        raise typer.BadParameter("only with --from-github", param_hint="--repo/--run-id")
    if not _api_extra_ready():
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT))
    from qasight_api.ui import ui_dir

    if ui_dir() is None:
        typer.echo(_BUILD_HINT, err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT))

    if from_github:
        _use_github_history(repo=repo, run_id=run_id)

    # Local only: binding anything wider is `qasight-api`'s job, with a token.
    os.environ["TESTO_API_HOST"] = "127.0.0.1"
    os.environ["TESTO_API_PORT"] = str(port)
    os.environ["TESTO_CORS_ORIGINS"] = _merged_cors(port)

    url = f"http://127.0.0.1:{port}/"
    typer.echo(f"QASight UI on {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    from qasight_api.main import run

    raise typer.Exit(run([]))


def _use_github_history(*, repo: str | None, run_id: str | None) -> None:
    """Download the Action's history and point this process at it (``DATABASE_URL``)."""
    from pathlib import Path

    from qasight.services.github_history import GithubHistoryError, download_history

    slug = (repo or Path.cwd().name).replace("/", "-")
    dest = Path.home() / ".cache" / "qasight" / "from-github" / slug
    try:
        db, artifact = download_history(cwd=Path.cwd(), dest=dest, repo=repo, run_id=run_id)
    except GithubHistoryError as exc:
        typer.echo(f"--from-github: {exc}", err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT)) from None
    os.environ["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    # Report copies and attachments stay in CI; don't mix in this machine's.
    os.environ["TESTO_HOME"] = str(dest)
    typer.echo(
        f"History from {artifact.name} (workflow run {artifact.run_id}, {artifact.created_at}). "
        "Runs, tests, flaky and Compare work; HTML reports and attachments stay in CI."
    )


def _has_api_extra() -> bool:
    import importlib

    importlib.invalidate_caches()
    try:
        import fastapi  # noqa: F401
        import uvicorn  # type: ignore[import-not-found]  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


def _api_extra_ready() -> bool:
    """True when the ``api`` extra is importable; in a terminal, offer to install it first."""
    import subprocess
    import sys

    if _has_api_extra():
        return True
    from qasight import __version__

    spec = f"qasight[api]=={__version__}"
    typer.echo("The web UI needs the api extra (FastAPI and uvicorn).", err=True)
    if not (sys.stdin.isatty() and sys.stdout.isatty()) or not typer.confirm(
        f"Install it into this Python now ({sys.executable} -m pip install '{spec}')?",
        default=True,
    ):
        typer.echo(f"Install it with: pip install '{spec}'", err=True)
        return False
    proc = subprocess.run(  # noqa: S603 — this interpreter's pip, fixed argv
        [sys.executable, "-m", "pip", "install", spec], check=False
    )
    if proc.returncode != 0:
        typer.echo(
            f"pip couldn't install it (exit {proc.returncode}). With pipx: "
            "pipx install --force 'qasight[api]'; with uv: uvx --from 'qasight[api]' qasight ui",
            err=True,
        )
        return False
    if _has_api_extra():
        return True
    typer.echo("Installed. Run `qasight ui` again.", err=True)
    return False
