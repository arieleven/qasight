"""``qasight scorecard`` — score a release with the repo review tools and gate it.

* ``scan TARGET``: run the switched-on tools on a folder or a git URL (any
  repository, cloned shallow) and record a score per tool for its version.
* ``import TOOL FILE``: record a report a tool already wrote (CI, mutation runs).
* ``show``: the scorecard of a version, as text, Markdown or JSON; with
  ``--min-score`` / ``--min-tool`` / ``--no-drop`` it is the release gate (exit 1).
* ``tools``: which tools exist, which are on, which are installed here.

See :mod:`qasight.services.scorecard` and the UI's Scorecard page.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer

from qasight.engine.exit_codes import EngineExitCode

app = typer.Typer(
    help="Score a release with the repo review tools (security, dependencies, API, web, load, tests) and gate it.",
    no_args_is_help=True,
)

_SERVER_HELP = (
    "Record on this QASight server instead of this machine's history "
    "(default: $TESTO_SERVER_URL). The token comes from $TESTO_API_TOKEN."
)


def _server(server: str | None) -> str | None:
    return (server or os.environ.get("TESTO_SERVER_URL") or "").strip() or None


def _fail(message: str, code: EngineExitCode = EngineExitCode.INVALID_INPUT) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code=int(code))


def _post_result(
    server: str, *, project: str, version: str, tool: str, result: Any, commit: str | None
) -> None:
    import requests

    token = os.environ.get("TESTO_API_TOKEN") or ""
    url = f"{server.rstrip('/')}/api/v1/scorecard/results"
    body = {
        "project": project,
        "version": version,
        "tool": tool,
        "score": result.score,
        "summary": result.summary,
        "metrics": dict(result.metrics),
        "counts": {k: v for k, v in result.counts.items() if v},
        "findings": [f.to_dict() for f in result.findings],
        "commit_sha": commit,
        "name": result.name,
        "category": result.category,
        "fingerprints": list(result.fingerprints),
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise _fail(f"could not reach {url}: {exc}", EngineExitCode.INFRA_FAILURE) from exc
    if resp.status_code >= 400:
        from qasight.services.ingest_client import _error_message

        code = (
            EngineExitCode.INVALID_INPUT if resp.status_code < 500 else EngineExitCode.INFRA_FAILURE
        )
        raise _fail(f"{url} answered {resp.status_code}: {_error_message(resp)}", code)


def _recorder(server: str | None, *, project: str, version: str, commit: str | None, source: str):
    if server:
        return lambda tool, result: _post_result(
            server, project=project, version=version, tool=tool, result=result, commit=commit
        )
    from qasight.services.scorecard.store import ScorecardStore

    store = ScorecardStore()
    return lambda tool, result: store.record(
        project=project, version=version, tool=tool, result=result, commit_sha=commit, source=source
    )


def _history_note() -> str:
    """Which database the results go to, so two folders' different histories aren't a surprise."""
    from sqlalchemy.engine import make_url

    from qasight.repository.db_config import resolve_database_url

    url = resolve_database_url()
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - only for the message
        return f"History: {url}"
    if parsed.get_backend_name() == "sqlite" and parsed.database:
        where = Path(parsed.database).expanduser().resolve()
        return f"History: {where} (set DATABASE_URL to use another)"
    return f"History: {parsed.render_as_string(hide_password=True)}"


def _tool_name(tool_id: str) -> str:
    """A tool's name, or the linter's key for one an engine ran (``megalinter:PYTHON_RUFF``)."""
    from qasight.services.scorecard.megalinter import name_from_id
    from qasight.services.scorecard.tools import TOOLS_BY_ID

    known = TOOLS_BY_ID.get(tool_id)
    return known.name if known else name_from_id(tool_id)


_NO_NEW_HELP = (
    "Exit 1 when a tool has a new high or critical finding since the previous version "
    "(old findings don't block)."
)


def _gate(
    card: Any,
    *,
    min_score: int | None,
    min_tool: int | None,
    no_drop: bool,
    no_new: bool = False,
) -> None:
    from qasight.services.scorecard.report import gate_failures

    failures = gate_failures(
        card, min_score=min_score, min_tool=min_tool, no_drop=no_drop, no_new=no_new
    )
    if failures:
        typer.echo("\nRelease gate FAILED:", err=True)
        for failure in failures:
            typer.echo(f"  - {failure}", err=True)
        raise typer.Exit(code=int(EngineExitCode.DOMAIN_FAILURE))
    if min_score is not None or min_tool is not None or no_drop or no_new:
        typer.echo("\nRelease gate passed.")


def _print_card(card: Any) -> None:
    def delta(score: int | None, prev: int | None) -> str:
        if score is None or prev is None:
            return ""
        d = score - prev
        return "  (=)" if d == 0 else f"  ({'+' if d > 0 else ''}{d})"

    head = f"{card.score}/100 ({card.grade})" if card.score is not None else "no score"
    typer.echo(f"{card.project} {card.version}: {head}{delta(card.score, card.previous_score)}")
    if card.previous_version:
        typer.echo(f"compared with {card.previous_version}")
    if card.coverage is not None:
        from qasight.services.scorecard.report import coverage_sentence

        typer.echo(coverage_sentence(card.coverage))
    for cat in card.categories:
        value = "off" if cat.score is None else str(cat.score)
        typer.echo(f"  {cat.name:<15} {value:>4}{delta(cat.score, cat.previous_score)}")
    typer.echo("")
    for tool in card.tools:
        off = "" if tool.enabled else "  [off]"
        typer.echo(
            f"  {tool.name:<20} {tool.score:>4}{delta(tool.score, tool.previous_score)}  {tool.summary}{off}"
        )


@app.command("tools")
def tools_cmd(
    on: str | None = typer.Option(
        None, "--on", help="Switch these tools on (comma-separated ids)."
    ),
    off: str | None = typer.Option(
        None, "--off", help="Switch these tools off (comma-separated ids)."
    ),
    reset: bool = typer.Option(False, "--reset", help="Back to every tool's default."),
    as_json: bool = typer.Option(False, "--json", help="Print the tools as JSON."),
) -> None:
    """List the tools: on or off, installed here or not, and what each reads."""
    from qasight.services.scorecard.scan import ScanError, known_tool_ids
    from qasight.services.scorecard.store import ScorecardStore
    from qasight.services.scorecard.tools import CATEGORIES, TOOLS

    store = ScorecardStore()
    try:
        if reset:
            store.reset_enabled()
        for tool_id in known_tool_ids(on) or []:
            store.set_enabled(tool_id, True)
        for tool_id in known_tool_ids(off) or []:
            store.set_enabled(tool_id, False)
    except ScanError as exc:
        raise _fail(str(exc)) from exc
    enabled = store.enabled()
    rows = [
        {
            "id": t.id,
            "name": t.name,
            "category": CATEGORIES[t.category],
            "kind": t.kind,
            "enabled": enabled[t.id],
            "installed": t.installed() if t.binary else None,
            "report": t.report,
            "install": t.install,
        }
        for t in TOOLS
    ]
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    for row in rows:
        state = "on " if row["enabled"] else "off"
        here = (
            ""
            if row["installed"] is None
            else ("installed" if row["installed"] else "not installed")
        )
        typer.echo(f"{state}  {row['id']:<18} {row['category']:<14} {row['kind']:<7} {here}")


@app.command("import")
def import_cmd(
    tool: str = typer.Argument(..., help="Tool id (see `qasight scorecard tools`)."),
    report: Path = typer.Argument(..., help="The report file the tool wrote."),
    project: str | None = typer.Option(
        None,
        "--project",
        help="Project name (default: the git remote's owner/name, or this folder's name).",
    ),
    version: str | None = typer.Option(
        None, "--version", help="Version (default: `git describe --tags --always`)."
    ),
    server: str | None = typer.Option(None, "--server", help=_SERVER_HELP),
    ignore: Path | None = typer.Option(
        None,
        "--ignore",
        help="Accepted findings to leave out (default: ./scorecard-ignore.yml when it exists).",
    ),
) -> None:
    """Record a report a tool already wrote as its score for a version."""
    from qasight.services.scorecard.ignore import (
        IgnoreFileError,
        apply_ignores,
        find_ignore_file,
        load_ignores,
    )
    from qasight.services.scorecard.parsers import ReportError
    from qasight.services.scorecard.scan import ScanError, resolve_target
    from qasight.services.scorecard.store import ScorecardError
    from qasight.services.scorecard.tools import TOOLS_BY_ID

    if tool not in TOOLS_BY_ID:
        raise _fail(f"unknown tool {tool!r}; known tools: {', '.join(TOOLS_BY_ID)}")
    if not report.is_file():
        raise _fail(f"{report} is not a file")
    try:
        target = resolve_target(str(Path.cwd()), project=project, version=version)
        ignore_path = ignore or find_ignore_file(Path.cwd())
        result = TOOLS_BY_ID[tool].parse(report)
        if ignore_path:
            result = apply_ignores(tool, result, load_ignores(ignore_path))
        _recorder(
            _server(server),
            project=target.project,
            version=target.version,
            commit=target.commit_sha,
            source="import",
        )(tool, result)
    except (ReportError, ScanError, ScorecardError, IgnoreFileError) as exc:
        raise _fail(f"{TOOLS_BY_ID[tool].name}: {exc}") from exc
    typer.echo(
        f"{target.project} {target.version}: {TOOLS_BY_ID[tool].name} {result.score}/100, {result.summary}"
    )
    if not _server(server):
        typer.echo(_history_note())


@app.command("scan")
def scan_cmd(
    target: str = typer.Argument(
        ".", help="A folder, or a git URL (https://github.com/torvalds/linux) to clone shallow."
    ),
    project: str | None = typer.Option(
        None, "--project", help="Project name (default: owner/name from the URL or git remote)."
    ),
    version: str | None = typer.Option(
        None, "--version", help="Version (default: `git describe --tags --always`)."
    ),
    ref: str | None = typer.Option(None, "--ref", help="Branch or tag to clone (git URLs only)."),
    url: str | None = typer.Option(
        None, "--url", help="A running copy of the app: enables ZAP, Lighthouse, axe and k6."
    ),
    openapi: str | None = typer.Option(
        None, "--openapi", help="OpenAPI schema URL or file: enables Schemathesis."
    ),
    tools: str | None = typer.Option(
        None, "--tools", help="Run only these tools (comma-separated ids), on or off."
    ),
    out: Path = typer.Option(
        Path("scorecard-reports"),
        "--out",
        help="Folder for the tools' reports and scorecard.md / .json.",
    ),
    timeout: int = typer.Option(900, "--timeout", min=10, help="Seconds each tool may run."),
    server: str | None = typer.Option(None, "--server", help=_SERVER_HELP),
    min_score: int | None = typer.Option(
        None, "--min-score", min=0, max=100, help="Exit 1 when the overall score is below this."
    ),
    min_tool: int | None = typer.Option(
        None, "--min-tool", min=0, max=100, help="Exit 1 when a switched-on tool scores below this."
    ),
    no_drop: bool = typer.Option(
        False, "--no-drop", help="Exit 1 when a tool scores lower than on the previous version."
    ),
    no_new: bool = typer.Option(False, "--no-new", help=_NO_NEW_HELP),
    ignore: Path | None = typer.Option(
        None,
        "--ignore",
        help="Accepted findings to leave out (default: scorecard-ignore.yml in the scanned repository).",
    ),
    megalinter_image: str | None = typer.Option(
        None,
        "--megalinter-image",
        help="MegaLinter image to run (default: the flavor for the main language, else the full image).",
    ),
) -> None:
    """Run the review tools on a repository and record its scorecard.

    MegaLinter (in Docker) picks and runs the linters and scanners that fit the
    repository's languages; without Docker, the scanners installed here run instead.
    """
    from qasight.services.scorecard.ignore import IgnoreFileError
    from qasight.services.scorecard.report import build_scorecard, to_markdown
    from qasight.services.scorecard.scan import (
        ScanError,
        finish,
        known_tool_ids,
        resolve_target,
        run_scan,
    )
    from qasight.services.scorecard.store import ScorecardStore
    from qasight.services.scorecard.tools import TOOLS_BY_ID

    def show(event: dict[str, Any]) -> None:
        if event["type"] == "clone":
            typer.echo(event["message"] + " …")
        elif event["type"] == "plan":
            langs = ", ".join(event["languages"]) or "none detected"
            typer.echo(
                f"Languages: {langs}. Running {len(event['tools'])} tool(s): {', '.join(event['tools']) or 'none'}"
            )
        elif event["type"] == "tool" and event["status"] == "running":
            detail = f" {event['message']}" if event.get("message") else " …"
            typer.echo(f"  {_tool_name(event['tool'])}:{detail}")
        elif event["type"] == "tool" and event["status"] == "done":
            score = f"{event['score']}/100, " if event.get("score") is not None else ""
            typer.echo(f"  {_tool_name(event['tool'])}: {score}{event['message']}")
        elif event["type"] == "tool":
            # The reason is printed once, in the "Not run" list at the end.
            word = "failed" if event["status"] == "error" else event["status"]
            typer.echo(f"  {_tool_name(event['tool'])}: {word}", err=True)

    try:
        only = known_tool_ids(tools)
        scan_target = resolve_target(
            target, project=project, version=version, ref=ref, on_event=show
        )
    except ScanError as exc:
        raise _fail(str(exc)) from exc
    store = None if _server(server) else ScorecardStore()
    try:
        enabled = store.enabled() if store else {t: True for t in TOOLS_BY_ID}
        typer.echo(f"Scanning {scan_target.project} {scan_target.version} ({scan_target.repo})")
        outcome = run_scan(
            scan_target,
            out_dir=out.resolve(),
            enabled=enabled,
            record=_recorder(
                _server(server),
                project=scan_target.project,
                version=scan_target.version,
                commit=scan_target.commit_sha,
                source="scan",
            ),
            only=only,
            url=url,
            openapi=openapi,
            timeout_s=timeout,
            on_event=show,
            ignore_file=ignore,
            engine_image=megalinter_image,
            record_coverage=(
                (
                    lambda o: store.record_coverage(
                        project=scan_target.project,
                        version=scan_target.version,
                        expected=o.expected,
                        missing=o.missing(),
                        languages=sorted(o.languages),
                    )
                )
                if store is not None
                else None
            ),
        )
    except IgnoreFileError as exc:
        raise _fail(str(exc)) from exc
    finally:
        finish(scan_target)

    not_run = outcome.not_run()
    if not_run:
        typer.echo("\nNot run:")
        for tool_id, why in not_run.items():
            typer.echo(f"  {_tool_name(tool_id)}: {why}")
    if outcome.ignore_file:
        typer.echo(f"\nIgnored findings listed in {outcome.ignore_file}.")
    if store is None:
        typer.echo(f"\nRecorded {len(outcome.results)} result(s) on {_server(server)}.")
        return
    card = build_scorecard(scan_target.project, scan_target.version, store=store)
    if card is None:
        raise _fail(
            "No tool produced a result, so there is no scorecard. Install a tool from the list above.",
            EngineExitCode.DOMAIN_FAILURE,
        )
    markdown = to_markdown(card, skipped=not_run)
    (out / "scorecard.md").write_text(markdown, encoding="utf-8")
    (out / "scorecard.json").write_text(
        json.dumps(asdict(card), indent=2, default=str), encoding="utf-8"
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")
    typer.echo("")
    _print_card(card)
    typer.echo(f"\nWrote {out / 'scorecard.md'}. Open it in the UI: qasight ui, then Scorecard.")
    typer.echo(_history_note())
    _gate(card, min_score=min_score, min_tool=min_tool, no_drop=no_drop, no_new=no_new)


_SHOW_FORMATS = ("text", "markdown", "json", "sarif", "html", "svg")


@app.command("show")
def show_cmd(
    project: str | None = typer.Option(
        None, "--project", help="Project (default: the last one scored)."
    ),
    version: str | None = typer.Option(None, "--version", help="Version (default: its latest)."),
    fmt: str = typer.Option(
        "text",
        "--format",
        help="text, markdown, json, sarif (GitHub code scanning), html (one page) or svg (badge).",
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write to this file instead of printing."
    ),
    min_score: int | None = typer.Option(
        None, "--min-score", min=0, max=100, help="Exit 1 when the overall score is below this."
    ),
    min_tool: int | None = typer.Option(
        None, "--min-tool", min=0, max=100, help="Exit 1 when a switched-on tool scores below this."
    ),
    no_drop: bool = typer.Option(
        False, "--no-drop", help="Exit 1 when a tool scores lower than on the previous version."
    ),
    no_new: bool = typer.Option(False, "--no-new", help=_NO_NEW_HELP),
) -> None:
    """Show a version's scorecard; with a threshold it is the release gate."""
    from qasight.services.scorecard import export
    from qasight.services.scorecard.report import build_scorecard, to_markdown

    if fmt not in _SHOW_FORMATS:
        raise _fail(f"--format is {', '.join(_SHOW_FORMATS[:-1])} or {_SHOW_FORMATS[-1]}")
    card = build_scorecard(project, version)
    if card is None:
        raise _fail(
            "No scorecard yet for that project and version. Run `qasight scorecard scan` first.",
            EngineExitCode.DOMAIN_FAILURE,
        )
    if fmt == "text" and output is None:
        _print_card(card)
        typer.echo(_history_note())
    else:
        if fmt in ("markdown", "text"):
            text = to_markdown(card)
        elif fmt == "sarif":
            text = json.dumps(export.to_sarif(card), indent=2)
        elif fmt == "html":
            text = export.to_html(card)
        elif fmt == "svg":
            text = export.badge_svg(card)
        else:
            text = json.dumps(asdict(card), indent=2, default=str)
        if output:
            output.write_text(text, encoding="utf-8")
            typer.echo(f"Wrote {output}")
        else:
            typer.echo(text)
    _gate(card, min_score=min_score, min_tool=min_tool, no_drop=no_drop, no_new=no_new)
