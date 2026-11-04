"""``qasight issues sync`` — open GitHub issues for a cycle's flaky tests and close fixed ones.

The same pass runs after every recorded run when ``TESTO_ISSUES=github``
(:mod:`qasight.services.flaky_issues`); this command runs it now, for one
cycle, and prints what it did. The token comes from the environment only.
"""

from __future__ import annotations

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(
    help="GitHub issues for flaky tests: one per flaky test, closed once it is stable.",
    no_args_is_help=True,
)


@app.command("sync")
def sync(
    cycle: str = typer.Option(..., "--cycle", help="Cycle whose flaky tests to sync."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what would be opened or closed; change nothing on GitHub."
    ),
    new: bool = typer.Option(
        False, "--new", help="Open issues only for tests the latest run made flaky (like the hook)."
    ),
    repo: str | None = typer.Option(
        None,
        "--repo",
        help="owner/name (default: $TESTO_ISSUES_REPO, else the repository the runs recorded).",
    ),
) -> None:
    """Open an issue for each flaky test that has none; close issues of tests that are stable."""
    from dataclasses import replace

    from qasight.services import flaky_issues
    from qasight.services.github_issues import GitHubClient

    name = cycle.strip()
    config = flaky_issues.config_from_env()
    if repo:
        config = replace(config, repository=repo.strip())
    checkout, repository = flaky_issues.resolve_repository(name, config)
    if repository is None:
        typer.echo(
            f"no GitHub repository for {name}: pass --repo owner/name or set "
            f"{flaky_issues.REPO_ENV}",
            err=True,
        )
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    if not config.token and not dry_run:
        typer.echo(f"not set: {' or '.join(flaky_issues.TOKEN_ENVS)}", err=True)
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    client = GitHubClient(token=config.token, repository=repository, api_url=config.api_url)
    result = flaky_issues.sync_cycle(
        cycle=name,
        client=client,
        config=config,
        repository=repository,
        checkout=checkout,
        only_new=new,
        dry_run=dry_run,
    )
    lines = flaky_issues.describe(result)
    prefix = "dry run: " if dry_run else ""
    typer.echo(f"{prefix}{repository}, cycle {name}")
    for line in lines:
        typer.echo(f"  {line}")
    if not result.actions and not result.warnings:
        typer.echo("  nothing to do")
    raise typer.Exit(
        code=int(EngineExitCode.INFRA_FAILURE) if result.warnings else int(EngineExitCode.SUCCESS)
    )
