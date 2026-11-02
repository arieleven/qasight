"""``testo ai`` — AI failure summaries from the command line.

``setup`` picks a provider this machine already has (an API key in the
environment, or the Claude Code CLI) and saves it where the web UI's AI
settings page keeps it; ``status`` shows what is set; ``summary`` writes the
summary of a failed run. See :mod:`testo_core.services.ai.cli_settings`.
"""

from __future__ import annotations

import typer

from testo_core.engine.exit_codes import EngineExitCode

app = typer.Typer(no_args_is_help=True)


@app.command("setup")
def setup(
    provider: str = typer.Option(
        None,
        "--provider",
        help="openai, anthropic, openai_compatible or claude_code (default: detect).",
    ),
    model: str = typer.Option(None, "--model", help="Model name (default: the provider's)."),
    base_url: str = typer.Option(
        None, "--base-url", help="With openai_compatible: the endpoint (Ollama, Groq, Gemini...)."
    ),
    key_env: str = typer.Option(
        None,
        "--key-env",
        help="Environment variable that holds the API key (the key itself is never saved).",
    ),
    share_test_source: bool = typer.Option(
        False,
        "--share-test-source",
        help="Also send a short excerpt of each failing test's own source with the summary "
        "prompt (off by default; with a cloud provider it goes to that provider).",
    ),
) -> None:
    """Turn on AI failure summaries with a provider this machine already has."""
    from testo_core.services.ai.cli_settings import PROVIDERS, detect_settings, save_settings
    from testo_core.services.ai.integration_settings import AiIntegrationSettings

    if provider:
        name = provider.strip().lower()
        if name not in PROVIDERS:
            raise typer.BadParameter(f"pick one of {', '.join(PROVIDERS)}", param_hint="--provider")
        settings = AiIntegrationSettings(enabled=True, provider=name)  # type: ignore[arg-type]
        why = "--provider"
    else:
        found = detect_settings()
        if found is None:
            typer.echo(
                "No AI provider found on this machine. Set OPENAI_API_KEY, ANTHROPIC_API_KEY or "
                "GEMINI_API_KEY (Gemini has a free tier), install the Claude Code CLI, or name "
                "one with --provider.",
                err=True,
            )
            raise typer.Exit(int(EngineExitCode.INVALID_INPUT))
        settings, why = found.settings, found.why
    from dataclasses import replace

    settings = replace(
        settings,
        model=model or settings.model,
        base_url=base_url or settings.base_url,
        api_key_env_var=key_env or settings.api_key_env_var,
        share_test_source=share_test_source,
    )
    try:
        path = save_settings(settings)
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"Not saved: {exc}", err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT)) from None
    typer.echo(f"AI summaries on: {settings.provider} {settings.model} ({why}).")
    if share_test_source:
        where = (
            "Claude, through your own Claude Code login"
            if settings.provider == "claude_code"
            else f"the {settings.provider} provider"
        )
        typer.echo(f"Test source: on. Excerpts of failing tests are sent to {where}.")
    typer.echo(f"Saved to {path}; the UI's AI settings page shows the same.")
    typer.echo("Try it: testo ai summary  (the latest failed run)")


@app.command("status")
def status() -> None:
    """Show the AI provider in use, and where its settings come from."""
    from testo_core.services.ai.cli_settings import settings_from_env, settings_store

    source = "TESTO_AI_PROVIDER" if settings_from_env() else "the AI settings file"
    settings = settings_store().get()
    if not settings.enabled:
        typer.echo("AI summaries are off. Turn them on with: testo ai setup")
        return
    key = settings.to_provider_config()
    key_var = key.resolved_api_key_env_var() if settings.provider != "claude_code" else ""
    typer.echo(f"AI summaries on: {settings.provider} {settings.model} (from {source})")
    shared = "on (failing tests' excerpts)" if settings.share_test_source else "off"
    typer.echo(f"Test source: {shared}")
    if key_var:
        import os

        present = "set" if os.environ.get(key_var) else "NOT set"
        typer.echo(f"Key: ${key_var} ({present})")


@app.command("summary")
def summary(
    cycle: str = typer.Option(None, "--cycle", help="The cycle's latest run (default: any cycle)."),
    run_id: str = typer.Option(None, "--run-id", help="This run instead of the latest."),
    refresh: bool = typer.Option(False, "--refresh", help="Ask again even if one is stored."),
) -> None:
    """Print the AI summary of a failed run (generated and stored on first use)."""
    from testo_core.services.ai_run_summary import summarize

    result = summarize(cycle=cycle, run_id=run_id, refresh=refresh)
    if result.text is None:
        typer.echo(result.reason, err=True)
        raise typer.Exit(int(EngineExitCode.INVALID_INPUT))
    typer.echo(result.text)
