"""One switch for machine-readable output, on by itself for coding agents.

Machine output used to depend on the command: ``--ci`` on ``run`` and ``ingest``,
``--json`` on some read commands, ``--format`` on ``scorecard show``, and nothing
on ``summary`` or ``doctor``. Now every command that reports something takes
``--json`` (``run`` and ``ingest`` keep ``--ci``, which means the same), and:

* ``QASIGHT_OUTPUT=json`` turns it on for a whole session (``text`` turns it off);
* a coding agent is detected and gets it without asking. Claude Code sets
  ``CLAUDECODE=1`` in the shell it runs commands in; ``QASIGHT_AGENT=1`` says the
  same for any other agent.

Commands whose plain output is meant for a shell (``split --index``, ``affected
--list``, ``lease take``) only switch on an explicit flag, so ``pytest $(qasight
split ...)`` keeps working inside an agent.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

OUTPUT_ENV_VARS: tuple[str, ...] = ("QASIGHT_OUTPUT", "TESTO_OUTPUT")
AGENT_ENV_VARS: tuple[str, ...] = ("CLAUDECODE", "QASIGHT_AGENT")

_ON = {"1", "true", "yes", "on"}


def agent_detected(env: Mapping[str, str] | None = None) -> bool:
    """Whether a coding agent is running this command."""
    env = os.environ if env is None else env
    return any((env.get(name) or "").strip().lower() in _ON for name in AGENT_ENV_VARS)


def output_mode(env: Mapping[str, str] | None = None) -> str:
    """``"json"`` or ``"text"``: the session's choice, else ``"json"`` for an agent."""
    env = os.environ if env is None else env
    for name in OUTPUT_ENV_VARS:
        value = (env.get(name) or "").strip().lower()
        if value in {"json", "ndjson", "machine"}:
            return "json"
        if value in {"text", "human", "plain", "rich"}:
            return "text"
    return "json" if agent_detected(env) else "text"


def wants_json(flag: bool = False, env: Mapping[str, str] | None = None) -> bool:
    """``--json`` given, or machine output chosen for the session (or an agent)."""
    return bool(flag) or output_mode(env) == "json"
