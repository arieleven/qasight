"""``extra_env`` values and the ``if:`` grammar (P7, P8 in docs/engine-cli-review.md)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.if_expr import evaluate, problem
from qasight.config.loader import load_config

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _load(tmp_path: Path, stage_extra: str):  # type: ignore[no-untyped-def]
    path = tmp_path / "qasight.yaml"
    path.write_text(
        textwrap.dedent(
            """\
            version: 1
            cycles:
              c:
                stages:
                  - name: s
                    equipment: pytest
            """
        )
        + textwrap.indent(textwrap.dedent(stage_extra), " " * 8),
        encoding="utf-8",
    )
    return load_config(path)


def test_extra_env_yaml_scalars_reach_the_process_as_shell_style_text(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "extra_env: {A: 1, B: true, C: false, D: null, E: 1.5, F: text}\n")
    assert dict(cfg.cycles["c"].stages[0].extra_env) == {
        "A": "1",
        "B": "true",
        "C": "false",
        "D": "",
        "E": "1.5",
        "F": "text",
    }


@pytest.mark.parametrize("value", ["[1, 2]", "{x: 1}"])
def test_extra_env_lists_and_mappings_are_config_errors(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigValidationError, match="'extra_env' value for 'A'"):
        _load(tmp_path, f"extra_env: {{A: {value}}}\n")


def test_an_env_value_containing_the_operator_does_not_change_the_comparison() -> None:
    assert evaluate('${env:X} == "a==b"', env={"X": "a==b"}) is True
    assert evaluate('${env:X} != "a==b"', env={"X": "a==b"}) is False
    assert evaluate("${env:X}", env={"X": "a==b"}) is True


@pytest.mark.parametrize(
    ("expr", "env", "expected"),
    [
        ("${env:CI}", {"CI": "true"}, True),
        ("${env:CI}", {"CI": "0"}, False),
        ("${env:CI}", {}, False),
        ("${env:CI:-yes}", {}, True),
        ('${env:BRANCH} == "main"', {"BRANCH": "main"}, True),
        ("${env:BRANCH} != 'main'", {"BRANCH": "dev"}, True),
        ('"has space" == "has space"', {}, True),
    ],
)
def test_the_supported_grammar(expr: str, env: dict[str, str], expected: bool) -> None:
    assert problem(expr) is None
    assert evaluate(expr, env=env) is expected


@pytest.mark.parametrize(
    "expr",
    [
        "${env:A} and ${env:B}",
        "${env:A} or ${env:B}",
        "not ${env:A}",
        "${env:A} && ${env:B}",
        '${env:A} == "x" == "y"',
        "(${env:A})",
        '== "x"',
    ],
)
def test_anything_else_is_a_config_error(tmp_path: Path, expr: str) -> None:
    assert problem(expr) is not None
    with pytest.raises(ConfigValidationError, match="is not supported"):
        _load(tmp_path, f"if: '{expr}'\n" if "'" not in expr else f'if: "{expr}"\n')
