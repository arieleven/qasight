"""``qasight version`` — print the installed qasight version."""

from __future__ import annotations

import importlib.metadata as md

import typer


def resolve_version() -> str:
    try:
        return md.version("qasight")
    except md.PackageNotFoundError:
        return "0.0.0+source"


def version() -> None:
    typer.echo(f"qasight {resolve_version()}")
