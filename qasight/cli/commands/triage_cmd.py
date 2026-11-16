"""``qasight triage accuracy`` — how often a cause suggested from similar failures would be right.

See :mod:`qasight.services.similar_causes`. Each marked cause is suggested from
all the others (leave-one-out) and compared with what the team actually marked.
"""

from __future__ import annotations

import json

import typer

app = typer.Typer(
    help="Failure causes the team marked, and the suggestions made from them.", no_args_is_help=True
)


@app.command("accuracy")
def accuracy(
    cycle: str | None = typer.Option(None, "--cycle", help="Only this cycle's marked causes."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """Replay the marked causes and print the suggestions' precision and coverage."""
    from qasight.services.similar_causes import evaluate, examples_from_store

    result = evaluate(examples_from_store(cycle))
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "examples": result.examples,
                    "suggested": result.suggested,
                    "correct": result.correct,
                    "precision": result.precision,
                    "coverage": result.coverage,
                    "by_cause": {
                        k: {"suggested": s, "correct": c} for k, (s, c) in result.by_cause.items()
                    },
                }
            )
        )
        return
    if result.examples < 2:
        typer.echo(
            "Mark the cause of a few failures first: each one is an example the others learn from."
        )
        return
    precision = f"{result.precision:.0%}" if result.precision is not None else "-"
    coverage = f"{result.coverage:.0%}" if result.coverage is not None else "-"
    typer.echo(
        f"{result.examples} marked causes: a suggestion for {result.suggested} ({coverage}), "
        f"right {result.correct} times ({precision})."
    )
    for cause, (suggested, correct) in result.by_cause.items():
        typer.echo(f"  {cause}: {correct}/{suggested} right")
