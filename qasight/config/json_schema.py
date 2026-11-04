"""JSON Schema for ``qasight.yaml``, for editor autocomplete and validation.

The loader (:mod:`qasight.config.loader`) stays the source of truth: it is
what accepts or rejects a config. This schema describes the same keys so that
editors with a YAML language server (VS Code, JetBrains, Neovim) can suggest
them and flag typos while you type. It is built from the constants in
:mod:`qasight.config.schema`, so adding a built-in framework, tier or
reporter there updates it; ``scripts/export_config_schema.py --check`` fails
CI when the committed ``schema/qasight.schema.json`` is stale.

Only the canonical ``cycles:`` layout is described. Legacy ``plans:`` and
``runs:`` still load but are not suggested.
"""

from __future__ import annotations

from typing import Any

from qasight.config.schema import (
    DEFAULT_TIER_BY_FRAMEWORK,
    SUPPORTED_FRAMEWORKS,
    SUPPORTED_REPORTER_TYPES,
    SUPPORTED_TIERS,
    Defaults,
)
from qasight.resources.pools import DEFAULT_HOOK_TIMEOUT_S, DEFAULT_LEASE_TTL_S

SCHEMA_ID = "https://raw.githubusercontent.com/arieleven/qasight/main/schema/qasight.schema.json"

# Resource pool and key names (qasight.resources.pools).
_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}$"
_NAME_PATTERN = r"^(?![/\\])(?![A-Za-z]:)(?!.*(^|[/\\])\.\.([/\\]|$)).+$"


_GLOBS: dict[str, Any] = {
    "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
}


def _open_enum(values: frozenset[str], description: str) -> dict[str, Any]:
    """Suggest the built-in values but accept any string (plugins add their own)."""
    return {
        "description": description,
        "anyOf": [{"enum": sorted(values)}, {"type": "string", "minLength": 1}],
    }


def _env() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Extra environment variables for the test process.",
        "additionalProperties": {"type": ["string", "number", "boolean"]},
    }


def _timeout(default: float | None) -> dict[str, Any]:
    return {
        "type": ["number", "null"],
        "exclusiveMinimum": 0,
        "default": default,
        "description": "Kill the stage after this many seconds. null means no limit.",
    }


def build_json_schema() -> dict[str, Any]:
    """Return the JSON Schema (draft-07) for a ``qasight.yaml`` file."""
    defaults = Defaults()
    tier_defaults = ", ".join(f"{k}={v}" for k, v in sorted(DEFAULT_TIER_BY_FRAMEWORK.items()))

    stage = {
        "type": "object",
        "description": "One step of a cycle: a test runner and its arguments.",
        "required": ["name"],
        "anyOf": [{"required": ["equipment"]}, {"required": ["framework"]}],
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "pattern": _NAME_PATTERN,
                "description": "Stage name; also its artifacts folder, so it must be a relative path.",
            },
            "equipment": _open_enum(
                SUPPORTED_FRAMEWORKS,
                "Test runner for this stage. 'command' runs any program given in 'args'. "
                "Plugins can add more.",
            ),
            "framework": _open_enum(SUPPORTED_FRAMEWORKS, "Legacy name for 'equipment'."),
            "target_repo": {
                "type": "string",
                "description": "Directory the runner starts in, relative to this file. "
                "Defaults to defaults.target_repo.",
            },
            "args": {
                "description": "Arguments for the runner, as a list or one shell-style string. "
                "For 'command' this is the whole command line.",
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": ["string", "number"]}},
                ],
            },
            "workers": {
                "description": "Parallel workers (pytest with xdist, behavex, playwright, jest, vitest). "
                "Defaults to defaults.workers. 'auto' (with resources per worker or test): "
                "as many as the pool has free keys.",
                "oneOf": [{"type": "integer", "minimum": 1}, {"const": "auto"}],
            },
            "resources": {
                "description": "A pool from the top-level 'resources' that this stage's keys "
                "come from: its name, or an object.",
                "oneOf": [
                    {"type": "string", "pattern": _KEY_PATTERN},
                    {
                        "type": "object",
                        "required": ["pool"],
                        "properties": {
                            "pool": {"type": "string", "pattern": _KEY_PATTERN},
                            "per": {
                                "enum": ["worker", "test", "stage"],
                                "default": "worker",
                                "description": "Who leases a key: each test worker (pytest, or "
                                "`qasight lease run`), each test (pytest only), or the stage itself.",
                            },
                            "labels": {"type": "array", "items": {"type": "string"}},
                            "wait": {
                                "description": "How long to wait for a free key: seconds, "
                                "'90s', '10m', '2h' or 'forever'.",
                                "default": "30m",
                                "type": ["number", "string"],
                            },
                        },
                        "additionalProperties": False,
                    },
                ],
            },
            "timeout_s": _timeout(defaults.timeout_s),
            "if": {
                "type": "string",
                "description": "Run the stage only when this expression is true.",
            },
            "extra_env": _env(),
            "tier": {
                "enum": sorted(SUPPORTED_TIERS),
                "description": "Test-pyramid tier the stage's tests count under. "
                f"Default by equipment: {tier_defaults}.",
            },
            "junit_xml": {
                "description": "Glob(s), relative to target_repo, of JUnit XML reports the "
                "stage writes. They are imported as results after the stage.",
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                ],
            },
        },
        "additionalProperties": False,
    }

    quarantine = {
        "type": "array",
        "description": "Tests whose failures don't fail their stage. They still run and still "
        "show as failed in results.",
        "items": {
            "anyOf": [
                {
                    "type": "string",
                    "minLength": 1,
                    "description": "fnmatch glob on the test's name or full name.",
                },
                {
                    "type": "object",
                    "required": ["test"],
                    "properties": {
                        "test": {
                            "type": "string",
                            "minLength": 1,
                            "description": "fnmatch glob on the test's name or full name.",
                        },
                        "reason": {"type": "string"},
                        "owner": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ]
        },
    }

    cycle = {
        "type": "object",
        "description": "A named list of stages, run with `qasight run --cycle <name>`.",
        "required": ["stages"],
        "properties": {
            "description": {"type": "string"},
            "stages": {"type": "array", "minItems": 1, "items": {"$ref": "#/definitions/stage"}},
            "quarantine": {
                "$ref": "#/definitions/quarantine",
                "description": "Added to the top-level quarantine list for this cycle.",
            },
            "max_parallel_stages": {
                "type": "integer",
                "minimum": 1,
                "default": 1,
                "description": "How many stages may run at once. 1 (the default) runs them in order.",
            },
            "trigger": {
                "type": "object",
                "description": "Run the cycle only when files matching these globs changed.",
                "required": ["paths"],
                "properties": {
                    "paths": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                        "description": "Globs relative to this file's directory.",
                    },
                    "since_ref": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Git ref to diff against, e.g. origin/main.",
                    },
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }

    reporter = {
        "type": "object",
        "description": "A post-run reporter. Keys other than 'type' are its options.",
        "required": ["type"],
        "properties": {
            "type": _open_enum(SUPPORTED_REPORTER_TYPES, "Reporter type. Plugins can add more."),
            "output_dir": {"type": "string", "description": "Relative to this file."},
            "out_dir": {"type": "string", "description": "Relative to this file."},
        },
        "additionalProperties": True,
    }

    key_name = {"type": "string", "pattern": _KEY_PATTERN}
    resource_item = {
        "description": "A key name, or a key with labels and data for the worker.",
        "oneOf": [
            key_name,
            {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": key_name,
                    "labels": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "data": {
                        "type": "object",
                        "description": "Passed to the worker as TESTO_RESOURCE_<NAME> variables.",
                        "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
                    },
                },
                "additionalProperties": False,
            },
        ],
    }
    command = {"type": "string", "minLength": 1}
    resource_pool = {
        "type": "object",
        "description": "Keys that parallel workers share (VMs, devices, accounts, seats). "
        "QASight hands each worker its own key; it never acts on the keys itself.",
        "oneOf": [{"required": ["items"]}, {"required": ["count"]}],
        "properties": {
            "items": {"type": "array", "minItems": 1, "items": resource_item},
            "count": {
                "type": "integer",
                "minimum": 1,
                "description": "N anonymous keys named <pool>-1 ... <pool>-N.",
            },
            "lease_ttl": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": DEFAULT_LEASE_TTL_S,
                "description": "Seconds without a heartbeat before a key is free again.",
            },
            "max_per_user": {
                "type": "integer",
                "minimum": 1,
                "description": "Most keys one person may hold at once.",
            },
            "reuse_delay": {
                "type": "number",
                "minimum": 0,
                "description": "Seconds an expired lease keeps its key out of the pool, so a "
                "frozen holder can notice it lost the key (default: lease_ttl / 3).",
            },
            "on_take": {
                **command,
                "description": "Your command, run with TESTO_RESOURCE_* set after a key is "
                "taken. If it fails, the key is taken out of the pool.",
            },
            "on_check": {
                **command,
                "description": "Your command, run after on_take: exit 0 if the key is usable. "
                "If it fails, the key is taken out of the pool.",
            },
            "on_release": {
                **command,
                "description": "Your command, run before a key is given back. If it works, the "
                "next taker gets TESTO_RESOURCE_CLEAN=1.",
            },
            "hook_timeout": {
                "type": "number",
                "exclusiveMinimum": 0,
                "default": DEFAULT_HOOK_TIMEOUT_S,
            },
            "provider": {
                "type": "object",
                "description": "Your own commands that look at the real keys. Only `qasight pool`, "
                "typed by a person, runs them; QASight never creates or deletes keys.",
                "properties": {
                    "list": {
                        **command,
                        "description": "Prints the keys that exist, one per line (read-only).",
                    },
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": SCHEMA_ID,
        "title": "qasight.yaml",
        "description": "Config for QASight (qasight): cycles of test stages and reporters.",
        "type": "object",
        "required": ["cycles"],
        "properties": {
            "version": {"type": "integer", "enum": [1], "default": 1},
            "defaults": {
                "type": "object",
                "description": "Values every stage inherits unless it sets its own.",
                "properties": {
                    "target_repo": {"type": "string", "default": str(defaults.target_repo)},
                    "artifacts_root": {"type": "string", "default": str(defaults.artifacts_root)},
                    "timeout_s": _timeout(defaults.timeout_s),
                    "workers": {"type": "integer", "minimum": 1, "default": defaults.workers},
                    "extra_env": _env(),
                },
                "additionalProperties": False,
            },
            "database": {
                "type": "object",
                "description": "Where run history is stored. DATABASE_URL overrides it.",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "SQLAlchemy URL, e.g. sqlite:////tmp/testo.db or "
                        "postgresql+psycopg://user:pass@host/db.",
                    }
                },
                "additionalProperties": False,
            },
            "reporters": {"type": "array", "items": {"$ref": "#/definitions/reporter"}},
            "quarantine": {"$ref": "#/definitions/quarantine"},
            "resources": {
                "type": "object",
                "description": "Pools of keys parallel workers lease (`qasight lease`).",
                "propertyNames": {"pattern": _KEY_PATTERN},
                "additionalProperties": {"$ref": "#/definitions/resource_pool"},
            },
            "history": {
                "type": "object",
                "description": "How long run history and report archives are kept. Without it "
                "nothing is deleted. Applied by `qasight clean --history` and by the API server on "
                "a timer; TESTO_RETENTION_DAYS / TESTO_RETENTION_RUNS_PER_CYCLE override it.",
                "properties": {
                    "retention": {
                        "type": "object",
                        "description": "A run is deleted (its row, its per-test rows and its "
                        "report copy under static/history/) when it breaks any limit here.",
                        "properties": {
                            "days": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "Delete runs that started more than this many "
                                "days ago.",
                            },
                            "runs_per_cycle": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "Keep at most this many of each cycle's newest "
                                "runs.",
                            },
                        },
                        "additionalProperties": False,
                    },
                    "max_file_mb": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Files bigger than this many MB (videos, traces) are left "
                        "out of a run's history copy and its report archive.",
                    },
                },
                "additionalProperties": False,
            },
            "affected": {
                "type": "object",
                "description": "How changed files map to tests, for `qasight affected` and "
                "`pytest --qasight-affected`. Anything not mapped runs every test.",
                "properties": {
                    "base": {
                        "type": "string",
                        "description": "Branch or commit to diff against. Default: the pull "
                        "request's target branch in CI, else origin/main.",
                    },
                    "coverage": {
                        "type": "string",
                        "description": "coverage.py JSON with test contexts (`pytest --cov "
                        "--cov-context=test`, then `coverage json --show-contexts`), relative "
                        "to this file.",
                    },
                    "run_all_when": dict(
                        _GLOBS,
                        description="Files whose change runs every test (added to conftest.py, "
                        "pyproject.toml, lockfiles and this file).",
                    ),
                    "ignore": dict(_GLOBS, description="Files whose change needs no test."),
                    "test_files": dict(
                        _GLOBS,
                        description="Names of test files (a changed one runs). Default: "
                        "test_*.py, *_test.py, *.test.*, *.spec.*, *_test.go, *_spec.rb, *.feature.",
                    ),
                    "rules": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["changed", "tests"],
                            "properties": {
                                "changed": dict(_GLOBS, description="Globs of changed files."),
                                "tests": dict(
                                    _GLOBS,
                                    description="Globs of test files, or pytest node ids, to run.",
                                ),
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
            "cycles": {
                "type": "object",
                "minProperties": 1,
                "propertyNames": {"not": {"const": "all"}, "pattern": _NAME_PATTERN},
                "additionalProperties": {"$ref": "#/definitions/cycle"},
            },
        },
        "additionalProperties": False,
        "definitions": {
            "stage": stage,
            "cycle": cycle,
            "quarantine": quarantine,
            "reporter": reporter,
            "resource_pool": resource_pool,
        },
    }
