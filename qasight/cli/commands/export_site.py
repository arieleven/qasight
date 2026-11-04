"""``qasight export-site`` — the run history as a read-only static site.

Writes the UI that ships in the wheel plus the history as JSON files
(:mod:`qasight_api.static_site`) to one folder that any static host can serve:
GitHub Pages (the Action's ``static-site`` input uploads it), GitLab Pages, an
S3 bucket, or ``python -m http.server``. The pages are the same as in
``qasight ui``; starting runs and saving settings are switched off.
"""

from __future__ import annotations

from pathlib import Path

import typer

from qasight.engine.exit_codes import EngineExitCode


def export_site(
    out: Path = typer.Option(
        Path("qasight-site"), "--out", "-o", help="Folder to write the site to (replaced)."
    ),
    base_path: str = typer.Option(
        None,
        "--base-path",
        help="Path the site is served under, e.g. /my-repo/ for GitHub Pages of a project "
        "(default: the path of --site-url, else /).",
    ),
    site_url: str = typer.Option(
        "", "--site-url", help="Public URL of the site, e.g. https://you.github.io/my-repo/."
    ),
    runs: int = typer.Option(10, "--runs", min=1, help="How many recent runs to include."),
    delta_pairs: int = typer.Option(
        20, "--compare-pairs", min=0, help="How many run comparisons to include."
    ),
    from_github: bool = typer.Option(
        False,
        "--from-github",
        help="Export the history the GitHub Action keeps (gh CLI) instead of this machine's.",
    ),
    repo: str = typer.Option(
        None, "--repo", help="With --from-github: owner/name (default: this checkout's)."
    ),
) -> None:
    """Write the UI and recent runs as a static site (GitHub Pages, any file host)."""
    from qasight.cli.commands.ui_cmd import _BUILD_HINT, _api_extra_ready, _use_github_history

    if repo and not from_github:
        raise typer.BadParameter("only with --from-github", param_hint="--repo")
    if not _api_extra_ready():
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT))
    from qasight_api.static_site import SiteError, build_site, normalize_base_path
    from qasight_api.ui import ui_dir

    bundle = ui_dir()
    if bundle is None:
        typer.echo(_BUILD_HINT, err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT))
    if from_github:
        _use_github_history(repo=repo, run_id=None)

    base = normalize_base_path(base_path, site_url)
    out_dir = out.expanduser().resolve()
    try:
        result = build_site(
            out_dir,
            ui_dir=bundle,
            base_path=base,
            site_url=site_url,
            run_limit=runs,
            delta_pairs=delta_pairs,
        )
    except SiteError as exc:
        typer.echo(f"export-site: {exc}", err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT)) from None
    typer.echo(
        f"Wrote a read-only site with {result.runs} run(s) to {result.out_dir} "
        f"(served under {result.base_path})."
    )
    typer.echo(
        f"Preview: python -m http.server -d {result.out_dir} 8080"
        + ("" if base == "/" else "  (paths assume " + base + "; use --base-path / to preview)")
    )
