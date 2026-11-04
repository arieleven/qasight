"""Guard against `docs/CLI Commands/Command Reference.md` documenting a
`qasight` subcommand that doesn't actually exist in the Typer app.

This test exists because doctor/clean/watch/init were fully documented
(flags, exit codes, sample output) while only living on an unmerged
feature branch — anyone on `main` got "No such command" for all four.
Nothing caught that drift. This test reads the same doc humans read and
asserts every `` `qasight <name>` `` command header resolves in the CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.main import get_command

from qasight.cli.app import app

_DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"
_COMMAND_REFERENCE = _DOCS_ROOT / "CLI Commands" / "Command Reference.md"

# Headers documenting entry points that are intentionally outside the `qasight`
# Typer tree (see "Other entry points (not `qasight` Typer tree)" in the doc).
_NON_TYPER_ENTRY_POINTS = {"qasight-api", "testo-ui"}

_HEADER_RE = re.compile(r"^## .*$", re.MULTILINE)
_COMMAND_TOKEN_RE = re.compile(r"`qasight ([a-z][a-z-]*)`")


def _documented_top_level_commands() -> set[str]:
    text = _COMMAND_REFERENCE.read_text(encoding="utf-8")
    names: set[str] = set()
    for header in _HEADER_RE.findall(text):
        names.update(_COMMAND_TOKEN_RE.findall(header))
    return names - _NON_TYPER_ENTRY_POINTS


@pytest.mark.contract
def test_command_reference_doc_exists() -> None:
    assert _COMMAND_REFERENCE.is_file(), (
        f"expected {_COMMAND_REFERENCE} to exist; update this test's path if the "
        "vault was reorganized"
    )


@pytest.mark.contract
def test_every_documented_command_resolves_in_cli() -> None:
    documented = _documented_top_level_commands()
    # Sanity check the parser itself isn't silently matching nothing.
    assert {"run", "report", "config", "version"} <= documented

    registered = set(get_command(app).commands.keys())
    missing = sorted(documented - registered)
    assert not missing, (
        f"docs/CLI Commands/Command Reference.md documents {missing} as `qasight` "
        "subcommands, but they are not registered in qasight/cli/app.py. Either "
        "wire the command up, or remove/update the doc section if it no longer "
        "applies."
    )
