"""The tiny ``if:`` grammar of a stage (P8 in docs/engine-cli-review.md).

``${env:VAR}`` (or ``${env:VAR:-default}``), optionally compared with ``==`` or
``!=`` to another value; a bare value is true unless it is empty, ``0``,
``false``, ``no`` or ``off``. Never uses ``eval``.

The operator is found in the expression as written, before ``${env:...}`` is
replaced, so an environment value that contains ``==`` can't change the
comparison. Anything else (``and``, ``or``, ``not``, ``&&``, parentheses, two
comparisons) is a config error instead of being read as one long string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

ENV_PATTERN = re.compile(r"\$\{env:(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_OPERATOR = re.compile(r"==|!=")
_FALSY = frozenset({"", "0", "false", "no", "off"})


def interpolate(value: str, *, env: Mapping[str, str]) -> str:
    """Replace every ``${env:NAME[:-DEFAULT]}`` token with its value."""

    def repl(match: re.Match[str]) -> str:
        return env.get(match.group("name"), match.group("default") or "")

    return ENV_PATTERN.sub(repl, value)


def _masked(expr: str) -> str:
    """*expr* with env tokens and quoted strings blanked (same length), so operators
    and spaces inside them are not seen."""
    out = ENV_PATTERN.sub(lambda m: "V" * len(m.group(0)), expr)
    return _QUOTED.sub(lambda m: "S" * len(m.group(0)), out)


def problem(expr: str) -> str | None:
    """Why *expr* is outside the grammar, or None when it is fine."""
    masked = _masked(expr)
    ops = _OPERATOR.findall(masked)
    if len(ops) > 1:
        return "only one == or != comparison is supported"
    sides = _OPERATOR.split(masked) if ops else [masked]
    for side in sides:
        text = side.strip()
        if not text and ops:
            return f"nothing on one side of {ops[0]!r}"
        if re.search(r"\s|&&|\|\||[()!<>]", text):
            return (
                'only `${env:VAR}`, `${env:VAR} == "value"` and `!=` are supported '
                "(no and / or / not)"
            )
    return None


def evaluate(expr: str | None, *, env: Mapping[str, str]) -> bool:
    if expr is None or not expr.strip():
        return True
    match = _OPERATOR.search(_masked(expr))
    if match is None:
        return interpolate(expr, env=env).strip().lower() not in _FALSY
    lhs, rhs = (
        interpolate(part, env=env).strip().strip('"').strip("'")
        for part in (expr[: match.start()], expr[match.end() :])
    )
    return (lhs == rhs) if match.group(0) == "==" else (lhs != rhs)
