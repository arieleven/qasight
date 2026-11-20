"""Project settings in ``qasight.yaml``, with the environment still winning.

Some behaviour could only be set with environment variables: auto-quarantine,
flaky-test issues, server schedules, redaction, publishing after a CI run. A
``settings:`` section in ``qasight.yaml`` now sets the same things in the file
the project already keeps::

    settings:
      quarantine: {auto: 0.3, release_after: 5}
      issues: {enabled: true, repo: acme/shop, labels: [flaky-test], close_after: 10}
      schedule: {digest: week, to: [slack], hour: 8}
      redact: {enabled: true, patterns: ["secret-[0-9]+"]}
      publish: true          # in CI, run/ingest publish the result (comment, summary)
      run_news: true         # end each run with what's new

Each key maps to the ``TESTO_*`` variable the code already reads, so nothing
else changes: the value is put in the environment only when that variable isn't
set, and an exported variable always wins. Secrets (tokens, webhooks) stay in the
environment only.

Every ``QASIGHT_*`` variable is also read as its ``TESTO_*`` name (the old
product name), so ``QASIGHT_RELEASE=2.3`` works like ``TESTO_RELEASE=2.3``.

:func:`effective_settings` says where each value came from, for ``qasight doctor``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

OLD_PREFIX: Final[str] = "TESTO_"
NEW_PREFIX: Final[str] = "QASIGHT_"


@dataclass(frozen=True)
class SettingKey:
    path: tuple[str, ...]  # ("quarantine", "auto")
    env: str
    kind: str  # "number" | "int" | "text" | "list" | "bool" | "issues"
    help: str


SETTINGS: Final[tuple[SettingKey, ...]] = (
    SettingKey(
        ("quarantine", "auto"),
        "TESTO_QUARANTINE_AUTO",
        "number",
        "Flip rate (0-1) at which a failing flaky test is quarantined on its own; 0 is off.",
    ),
    SettingKey(
        ("quarantine", "release_after"),
        "TESTO_QUARANTINE_RELEASE_AFTER",
        "int",
        "Clean runs in a row after which a quarantined test is released.",
    ),
    SettingKey(
        ("issues", "enabled"),
        "TESTO_ISSUES",
        "issues",
        "Open a GitHub issue for each test that turns flaky, close it once stable.",
    ),
    SettingKey(("issues", "repo"), "TESTO_ISSUES_REPO", "text", "owner/name for the issues."),
    SettingKey(("issues", "labels"), "TESTO_ISSUES_LABELS", "list", "Labels on each issue."),
    SettingKey(("issues", "assign"), "TESTO_ISSUES_ASSIGN", "text", "Assign issues (owners)."),
    SettingKey(
        ("issues", "close_after"),
        "TESTO_ISSUES_CLOSE_AFTER",
        "int",
        "Passing runs before an issue is closed.",
    ),
    SettingKey(
        ("schedule", "digest"), "TESTO_SCHEDULE_DIGEST", "text", "Server digest: week or day."
    ),
    SettingKey(("schedule", "to"), "TESTO_SCHEDULE_TO", "list", "Where the digest goes."),
    SettingKey(("schedule", "hour"), "TESTO_SCHEDULE_HOUR", "int", "Hour (0-23) it is sent."),
    SettingKey(("redact", "enabled"), "TESTO_REDACT", "bool", "Mask secrets in stored output."),
    SettingKey(("redact", "patterns"), "TESTO_REDACT_PATTERNS", "list", "Extra patterns."),
    SettingKey(
        ("publish",),
        "TESTO_PUBLISH",
        "bool",
        "In CI, run/ingest publish the result (PR comment, job summary, alerts).",
    ),
    SettingKey(("run_news",), "TESTO_RUN_NEWS", "bool", "End each run with what's new."),
)


def schema_properties() -> dict[str, Any]:
    """The ``settings:`` section for the JSON Schema (built from :data:`SETTINGS`)."""
    types: dict[str, dict[str, Any]] = {
        "number": {"type": "number", "minimum": 0, "maximum": 1},
        "int": {"type": "integer", "minimum": 0},
        "text": {"type": "string"},
        "list": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
        "bool": {"type": "boolean"},
        "issues": {"type": "boolean"},
    }
    top: dict[str, Any] = {}
    for key in SETTINGS:
        leaf = {**types[key.kind], "description": f"{key.help} Overridden by {key.env}."}
        if len(key.path) == 1:
            top[key.path[0]] = leaf
            continue
        group = top.setdefault(
            key.path[0], {"type": "object", "properties": {}, "additionalProperties": False}
        )
        group["properties"][key.path[1]] = leaf
    return top


def _env_value(key: SettingKey, value: Any) -> str | None:
    if value is None:
        return None
    if key.kind == "list":
        items = [value] if isinstance(value, str) else list(value)
        return ",".join(str(v).strip() for v in items if str(v).strip())
    if key.kind == "bool":
        return "1" if value is True else "0" if value is False else str(value)
    if key.kind == "issues":
        if value is True:
            return "github"
        if value is False:
            return ""
        return str(value)
    return str(value)


def _lookup(raw: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = raw
    for part in path:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def alias_new_names(env: MutableMapping[str, str]) -> list[str]:
    """Copy each ``QASIGHT_X`` to ``TESTO_X`` when that isn't set; returns the names copied."""
    copied: list[str] = []
    for name, value in list(env.items()):
        if not name.startswith(NEW_PREFIX):
            continue
        old = OLD_PREFIX + name[len(NEW_PREFIX) :]
        if old not in env:
            env[old] = value
            copied.append(old)
    return copied


def _settings_section(config: Path | None) -> Mapping[str, Any]:
    from qasight.config.database_section import discover_config_path, load_raw_config_dict

    path = config if config is not None else discover_config_path()
    if path is None:
        return {}
    try:
        raw = load_raw_config_dict(path)
    except Exception:  # noqa: BLE001 — a broken file is reported by the loader, not here
        return {}
    section = raw.get("settings") if isinstance(raw, Mapping) else None
    return section if isinstance(section, Mapping) else {}


# What apply_project_settings set, for effective_settings().
_FROM_FILE: dict[str, str] = {}


def apply_project_settings(
    env: MutableMapping[str, str] | None = None, *, config: Path | None = None
) -> dict[str, str]:
    """Put ``settings:`` values (and ``QASIGHT_*`` aliases) in *env* where unset.

    Returns the variables set from the file. Safe to call more than once.
    """
    env = os.environ if env is None else env
    alias_new_names(env)
    section = _settings_section(config)
    applied: dict[str, str] = {}
    for key in SETTINGS:
        value = _env_value(key, _lookup(section, key.path))
        if value is None or key.env in env:
            continue
        env[key.env] = value
        applied[key.env] = value
    _FROM_FILE.update(applied)
    return applied


@dataclass(frozen=True)
class EffectiveSetting:
    name: str  # "quarantine.auto"
    env: str
    value: str | None
    source: str  # "qasight.yaml" | "environment" | "default"


def effective_settings(
    env: Mapping[str, str] | None = None, keys: Iterable[SettingKey] = SETTINGS
) -> list[EffectiveSetting]:
    """Each setting's value and where it came from."""
    env = os.environ if env is None else env
    out: list[EffectiveSetting] = []
    for key in keys:
        value = env.get(key.env)
        if value is None:
            source = "default"
        elif _FROM_FILE.get(key.env) == value:
            source = "qasight.yaml"
        else:
            source = "environment"
        out.append(EffectiveSetting(".".join(key.path), key.env, value, source))
    return out
