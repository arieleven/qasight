"""``qasight config`` — validate or scaffold a qasight.yaml."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

from qasight.config.json_schema import SCHEMA_ID
from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(help="Validate or scaffold a qasight.yaml.", no_args_is_help=True)


_STARTER_YAML = f"""\
# yaml-language-server: $schema={SCHEMA_ID}
version: 1

defaults:
  target_repo: .
  artifacts_root: artifacts
  timeout_s: 600
  workers: 4

cycles:
  smoke-test:
    description: Run the pytest suite in tests/.
    stages:
      - name: api
        equipment: pytest
        args: ["-q", "tests"]
        # To select only tests marked smoke, replace args above with:
        # args: ["-q", "tests", "-m", "smoke", "--maxfail=1"]

  # Optional: enable after adding the feature file and its step definitions.
  # nightly-build:
  #   description: Full suite executed on cron.
  #   stages:
  #     - name: api
  #       equipment: pytest
  #       args: ["-q", "tests"]
  #     - name: ui-bdd
  #       equipment: behavex
  #       args: ["features/smoke.feature"]
  #       workers: 8
  #       timeout_s: 1800
"""


@app.command("validate")
def validate(
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a qasight.yaml file (defaults to discovery).",
    ),
    check_executables: bool = typer.Option(
        True,
        "--check-executables/--no-check-executables",
        help="Verify required test executables (pytest/behave/behavex) are available on PATH.",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Fail (exit 2) when the config has keys nothing reads, instead of warning.",
    ),
) -> None:
    """Load + resolve the config and print 'ok' (or exit non-zero with an error)."""
    from qasight.cli.ui.console import default_console
    from qasight.config.errors import ConfigError
    from qasight.config.loader import discover_and_load

    console = default_console()
    try:
        cfg = discover_and_load(config_path=config)
    except ConfigError as exc:
        console.print(f"[fail]config error:[/] {exc}")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT)) from exc

    for message in cfg.warnings:
        console.print(f"[{'fail' if strict else 'yellow'}]warning:[/] {message}")
    if strict and cfg.warnings:
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))

    if check_executables:
        missing = _missing_executables(cfg)
        if missing:
            console.print("[fail]missing executables:[/]")
            for name in missing:
                console.print(f"  - {name}")
            console.print(
                "[muted]hint:[/] install dependencies (e.g. `pip install -e .`) or ensure the active environment's bin/ is on PATH."
            )
            raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    console.print(
        f"[ok]ok[/] — version={cfg.version} cycles={len(cfg.cycles)} defaults_target={cfg.defaults.target_repo}"
    )


def _missing_executables(cfg) -> list[str]:  # type: ignore[no-untyped-def]
    # Collect the distinct equipment/framework names used across all cycles.
    used: set[str] = set()
    for cycle in cfg.cycles.values():
        for stage in cycle.stages:
            used.add(str(stage.framework))

    # Map equipment names to the expected CLI executable.
    exe_for = {
        "pytest": "pytest",
        "behave": "behave",
        "behavex": "behavex",
        "playwright": "npx",
        "jest": "npx",
        "vitest": "npx",
        "cypress": "npx",
    }
    missing: list[str] = []
    for fw in sorted(used):
        exe = exe_for.get(fw)
        if not exe:
            continue
        if shutil.which(exe) is None:
            missing.append(exe)
    return missing


@app.command("init")
def init(
    path: Path = typer.Option(
        Path("qasight.yaml"),
        "--path",
        "-p",
        help="Output path for the starter qasight.yaml.",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing file."),
) -> None:
    """Write a starter qasight.yaml (--path, default ./qasight.yaml)."""
    from qasight.cli.ui.console import default_console

    console = default_console()
    if path.exists() and not force:
        console.print(f"[fail]refusing to overwrite[/] {path} (use --force).")
        raise typer.Exit(code=int(EngineExitCode.INVALID_INPUT))
    path.write_text(_STARTER_YAML, encoding="utf-8")
    console.print(f"[ok]wrote starter config to[/] {path}")
