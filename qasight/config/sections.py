"""Parse the sections of a config file: defaults, cycles, stages, triggers,
quarantine rules and reporters.

`loader` reads the file and decides its shape (``cycles:`` or legacy ``runs:``);
the functions here turn each raw mapping into the frozen models in
:mod:`qasight.config.schema`, raising ``ConfigValidationError`` with the
offending key's location.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any

from qasight import plugins
from qasight.config.errors import ConfigValidationError
from qasight.config.if_expr import problem as if_problem
from qasight.config.schema import (
    DEFAULT_TIER_BY_FRAMEWORK,
    PARALLEL_FRAMEWORKS,
    SUPPORTED_FRAMEWORKS,
    SUPPORTED_TIERS,
    CycleTrigger,
    Defaults,
    Plan,
    QuarantineRule,
    ReporterSpec,
    Stage,
)
from qasight.resources.pools import ResourcePool, parse_stage_resources

logger = logging.getLogger(__name__)


def parse_defaults(raw: Any, *, config_dir: Path) -> Defaults:
    if not isinstance(raw, dict):
        raise ConfigValidationError("'defaults:' must be a mapping.")
    target_repo = _resolve_path(raw.get("target_repo", "."), config_dir=config_dir)
    artifacts_root = _resolve_path(raw.get("artifacts_root", "artifacts"), config_dir=config_dir)
    timeout_s = parse_timeout(raw.get("timeout_s", 600.0), where="defaults")
    workers = parse_workers(raw.get("workers", 4), where="defaults")
    extra_env = _normalise_env(raw.get("extra_env"))
    return Defaults(
        target_repo=target_repo,
        artifacts_root=artifacts_root,
        timeout_s=timeout_s,
        workers=workers,
        extra_env=extra_env,
        workers_explicit="workers" in raw,
    )


def parse_cycle(
    *,
    cycle_name: str,
    cycle_raw: dict[str, Any],
    defaults: Defaults,
    config_dir: Path,
    shared_quarantine: tuple[QuarantineRule, ...] = (),
    pools: Mapping[str, ResourcePool] | None = None,
) -> Plan:
    if not isinstance(cycle_raw, dict):
        raise ConfigValidationError(f"cycle {cycle_name!r} must be a mapping.")
    description = cycle_raw.get("description")
    stages_raw = cycle_raw.get("stages")
    if not isinstance(stages_raw, list) or not stages_raw:
        raise ConfigValidationError(f"cycle {cycle_name!r} must define a non-empty 'stages:' list.")
    stages = tuple(
        parse_stage(stage_raw=item, defaults=defaults, config_dir=config_dir, pools=pools)
        for item in stages_raw
    )
    _check_unique_stage_names(stages, cycle_name=cycle_name)
    trigger = _parse_trigger(cycle_raw.get("trigger"), cycle_name=cycle_name)
    quarantine = shared_quarantine + parse_quarantine(
        cycle_raw.get("quarantine"), where=f"cycle {cycle_name!r}"
    )
    max_parallel_stages = _parse_max_parallel_stages(
        cycle_raw.get("max_parallel_stages", 1), cycle_name=cycle_name, stages=stages
    )
    return Plan(
        name=cycle_name,
        description=description,
        stages=stages,
        trigger=trigger,
        quarantine=quarantine,
        max_parallel_stages=max_parallel_stages,
    )


def _check_unique_stage_names(stages: tuple[Stage, ...], *, cycle_name: str) -> None:
    # Each stage writes to artifacts/<cycle>/<stage>/; two stages with one name (or
    # names that differ only in case, one folder on macOS and Windows) would wipe
    # each other's results.
    seen: dict[str, str] = {}
    for stage in stages:
        key = stage.name.casefold()
        if key in seen:
            same = (
                "the same name" if seen[key] == stage.name else f"{seen[key]!r} and {stage.name!r}"
            )
            raise ConfigValidationError(
                f"cycle {cycle_name!r}: two stages have {same}; stage names must be "
                "unique (ignoring case), since each stage gets its own artifacts folder."
            )
        seen[key] = stage.name


def _parse_max_parallel_stages(raw: Any, *, cycle_name: str, stages: tuple[Stage, ...]) -> int:
    # bool is an int subclass; `max_parallel_stages: true` is a typo, not 1.
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ConfigValidationError(
            f"cycle {cycle_name!r}: 'max_parallel_stages' must be a whole number of at least 1."
        )
    if raw > 1:
        # Parallel stages write artifacts/<cycle>/<stage>/ at the same time, so two
        # stages with one name would overwrite each other's logs and results.
        names = [s.name for s in stages]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ConfigValidationError(
                f"cycle {cycle_name!r}: stage names must be unique when "
                f"max_parallel_stages > 1 (repeated: {', '.join(duplicates)})."
            )
    return raw


_QUARANTINE_KEYS: frozenset[str] = frozenset({"test", "reason", "owner"})


def parse_quarantine(raw: Any, *, where: str) -> tuple[QuarantineRule, ...]:
    """``quarantine:`` entries: a test name glob, or ``{test, reason?, owner?}``."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigValidationError(f"{where}: 'quarantine' must be a list.")
    rules: list[QuarantineRule] = []
    for i, item in enumerate(raw):
        label = f"{where}: quarantine[{i}]"
        if isinstance(item, str):
            item = {"test": item}
        if not isinstance(item, dict):
            raise ConfigValidationError(f"{label} must be a test name or a mapping with 'test'.")
        unknown = sorted(str(k) for k in item if k not in _QUARANTINE_KEYS)
        if unknown:
            raise ConfigValidationError(
                f"{label} has unknown key(s) {unknown}; allowed: {sorted(_QUARANTINE_KEYS)}"
            )
        test = item.get("test")
        if not isinstance(test, str) or not test.strip():
            raise ConfigValidationError(f"{label}: 'test' must be a non-empty string.")
        for key in ("reason", "owner"):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise ConfigValidationError(f"{label}: '{key}' must be a string.")
        rules.append(
            QuarantineRule(
                test=test.strip(),
                reason=(item.get("reason") or "").strip() or None,
                owner=(item.get("owner") or "").strip() or None,
            )
        )
    return tuple(rules)


def _parse_trigger(raw: Any, *, cycle_name: str) -> CycleTrigger | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigValidationError(f"cycle {cycle_name!r}: 'trigger' must be a mapping.")
    paths_raw = raw.get("paths")
    if not isinstance(paths_raw, list) or not paths_raw:
        raise ConfigValidationError(
            f"cycle {cycle_name!r}: 'trigger.paths' must be a non-empty list."
        )
    paths_out: list[str] = []
    for i, p in enumerate(paths_raw):
        if not isinstance(p, str) or not p.strip():
            raise ConfigValidationError(
                f"cycle {cycle_name!r}: trigger.paths[{i}] must be a non-empty string."
            )
        paths_out.append(p.strip())
    since_raw = raw.get("since_ref")
    since_ref: str | None = None
    if since_raw is not None:
        if not isinstance(since_raw, str) or not since_raw.strip():
            raise ConfigValidationError(
                f"cycle {cycle_name!r}: 'trigger.since_ref' must be a non-empty string."
            )
        since_ref = since_raw.strip()
    return CycleTrigger(paths=tuple(paths_out), since_ref=since_ref)


_REPORTER_PATH_OPTION_KEYS: frozenset[str] = frozenset({"output_dir", "out_dir"})


def parse_reporters(raw: Any, *, config_dir: Path) -> tuple[ReporterSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigValidationError("'reporters:' must be a list.")
    out: list[ReporterSpec] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ConfigValidationError(f"reporters[{idx}] must be a mapping.")
        reporter_type = str(item.get("type") or item.get("name") or "").strip().lower()
        if not reporter_type:
            raise ConfigValidationError(f"reporters[{idx}] is missing 'type'.")
        if reporter_type not in plugins.reporter_types():
            raise ConfigValidationError(
                f"reporters[{idx}] has unsupported type {reporter_type!r}; "
                f"supported: {sorted(plugins.reporter_types())}"
            )
        options: list[tuple[str, str]] = []
        for key, value in item.items():
            if key in ("type", "name") or value is None:
                continue
            key_str = str(key)
            if key_str in _REPORTER_PATH_OPTION_KEYS:
                options.append((key_str, str(_resolve_path(value, config_dir=config_dir))))
            else:
                options.append((key_str, str(value)))
        out.append(ReporterSpec(type=reporter_type, options=tuple(options)))
    return tuple(out)


def parse_stage(
    *,
    stage_raw: Mapping[str, Any],
    defaults: Defaults,
    config_dir: Path,
    pools: Mapping[str, ResourcePool] | None = None,
) -> Stage:
    if not isinstance(stage_raw, Mapping):
        raise ConfigValidationError("stage entries must be mappings.")
    name = str(stage_raw.get("name") or "").strip()
    equipment_raw = stage_raw.get("equipment")
    framework_raw = stage_raw.get("framework")
    if equipment_raw is not None and framework_raw is not None:
        if str(equipment_raw).strip() != str(framework_raw).strip():
            raise ConfigValidationError(
                f"stage {name or '<unnamed>'!r} defines both 'equipment' and 'framework' with different values."
            )
    framework = str((equipment_raw if equipment_raw is not None else framework_raw) or "").strip()
    if not name:
        raise ConfigValidationError("stage is missing 'name'.")
    validate_artifact_name(name, kind=f"stage {name!r} name")
    if not framework:
        raise ConfigValidationError(f"stage {name!r} is missing 'equipment' (legacy: 'framework').")
    if framework == "behave_native":
        framework = "behave"
    if framework not in plugins.framework_names():
        raise ConfigValidationError(
            f"stage {name!r} has unsupported framework {framework!r}; "
            f"supported: {sorted(plugins.framework_names())}"
        )

    target_repo_raw = stage_raw.get("target_repo")
    target_repo = (
        _resolve_path(target_repo_raw, config_dir=config_dir)
        if target_repo_raw is not None
        else defaults.target_repo
    )

    args_raw = stage_raw.get("args")
    if args_raw is None:
        args: tuple[str, ...] = ()
    elif isinstance(args_raw, str):
        import shlex

        args = tuple(shlex.split(args_raw))
    elif isinstance(args_raw, list):
        if not all(isinstance(a, (str, int, float)) for a in args_raw):
            raise ConfigValidationError(f"stage {name!r}: 'args' must be a list of scalars.")
        args = tuple(str(a) for a in args_raw)
    else:
        raise ConfigValidationError(f"stage {name!r}: 'args' must be a string or list.")

    resources = parse_stage_resources(
        stage_raw.get("resources"), pools=pools or {}, where=f"stage {name!r}"
    )
    workers_auto = str(stage_raw.get("workers", "")).strip().lower() == "auto"
    if workers_auto and (resources is None or resources.per == "stage"):
        raise ConfigValidationError(
            f"stage {name!r}: 'workers: auto' needs 'resources' with per: worker or per: test "
            "(it starts one worker per free key)."
        )
    if resources is not None and resources.per == "test" and framework != "pytest":
        raise ConfigValidationError(
            f"stage {name!r}: resources per: test works with pytest only; use per: worker "
            "or per: stage."
        )
    workers = (
        defaults.workers
        if workers_auto
        else parse_workers(stage_raw.get("workers", defaults.workers), where=f"stage {name!r}")
    )
    # Plugin adapters get ``workers`` in build_argv and decide for themselves.
    if (
        "workers" in stage_raw
        and framework in SUPPORTED_FRAMEWORKS
        and framework not in PARALLEL_FRAMEWORKS
    ):
        logger.warning(
            "stage %r: 'workers' has no effect on framework %r (it runs single-process).",
            name,
            framework,
        )
    timeout_s = parse_timeout(
        stage_raw.get("timeout_s", defaults.timeout_s), where=f"stage {name!r}"
    )
    if_expr = stage_raw.get("if")
    if if_expr is not None and not isinstance(if_expr, str):
        raise ConfigValidationError(f"stage {name!r}: 'if' must be a string.")
    if if_expr is not None and (why := if_problem(if_expr)):
        raise ConfigValidationError(f"stage {name!r}: 'if: {if_expr}' is not supported: {why}.")

    extra_env = _normalise_env(stage_raw.get("extra_env")) or defaults.extra_env

    tier_raw = stage_raw.get("tier")
    if tier_raw is None:
        tier = DEFAULT_TIER_BY_FRAMEWORK.get(framework, "unit")
    else:
        tier = str(tier_raw).strip().lower()
        if tier not in SUPPORTED_TIERS:
            raise ConfigValidationError(
                f"stage {name!r} has unsupported tier {tier!r}; supported: {sorted(SUPPORTED_TIERS)}"
            )

    junit_raw = stage_raw.get("junit_xml")
    if junit_raw is None:
        junit_xml: tuple[str, ...] = ()
    elif isinstance(junit_raw, str) and junit_raw.strip():
        junit_xml = (junit_raw.strip(),)
    elif (
        isinstance(junit_raw, list)
        and junit_raw
        and all(isinstance(p, str) and p.strip() for p in junit_raw)
    ):
        junit_xml = tuple(p.strip() for p in junit_raw)
    else:
        raise ConfigValidationError(
            f"stage {name!r}: 'junit_xml' must be a non-empty string or list of glob patterns."
        )
    for pattern in junit_xml:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ConfigValidationError(
                f"stage {name!r}: 'junit_xml' patterns must be relative to target_repo "
                f"and stay inside it (got {pattern!r})."
            )

    if framework == "command" and not args:
        raise ConfigValidationError(
            f"stage {name!r}: framework 'command' needs 'args' (the full command to run)."
        )
    if framework == "junit" and not junit_xml:
        raise ConfigValidationError(
            f"stage {name!r}: framework 'junit' needs 'junit_xml' (the reports to record)."
        )

    return Stage(
        name=name,
        framework=framework,
        target_repo=target_repo,
        args=args,
        workers=workers,
        timeout_s=timeout_s,
        if_expr=if_expr,
        extra_env=extra_env,
        tier=tier,
        junit_xml=junit_xml,
        resources=resources,
        workers_auto=workers_auto,
        workers_explicit="workers" in stage_raw or defaults.workers_explicit,
    )


def parse_timeout(value: Any, *, where: str) -> float | None:
    """``timeout_s``: seconds above zero, or null for no limit (as the JSON Schema says)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigValidationError(f"{where}: 'timeout_s' must be a number of seconds or null.")
    try:
        seconds = float(value)
    except ValueError:
        raise ConfigValidationError(
            f"{where}: 'timeout_s' must be a number of seconds or null (got {value!r})."
        ) from None
    if not seconds > 0 or seconds == float("inf"):
        raise ConfigValidationError(
            f"{where}: 'timeout_s' must be above 0 (got {value!r}); use null for no limit."
        )
    return seconds


def parse_workers(value: Any, *, where: str) -> int:
    """``workers``: a whole number, 1 or more (as the JSON Schema says)."""
    if isinstance(value, bool):
        raise ConfigValidationError(f"{where}: 'workers' must be a whole number, 1 or more.")
    try:
        workers = int(value)
    except (TypeError, ValueError):
        raise ConfigValidationError(
            f"{where}: 'workers' must be a whole number, 1 or more (got {value!r})."
        ) from None
    if workers < 1 or (isinstance(value, float) and value != workers):
        raise ConfigValidationError(
            f"{where}: 'workers' must be a whole number, 1 or more (got {value!r})."
        )
    return workers


def parse_version(value: Any) -> int:
    """Top-level ``version``: only 1 exists so far."""
    if isinstance(value, bool) or (value != 1 and str(value).strip() != "1"):
        raise ConfigValidationError(
            f"unsupported config 'version: {value}'; this qasight reads version 1."
        )
    return 1


def _resolve_path(value: Any, *, config_dir: Path) -> Path:
    if value is None:
        return config_dir
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (config_dir / p).resolve()


def validate_artifact_name(value: str, *, kind: str) -> None:
    """Reject artifact path names that can escape their containing directory."""
    text = str(value).strip()
    posix_text = text.replace("\\", "/")
    win = PureWindowsPath(text)
    if posix_text.startswith("/") or win.is_absolute() or win.drive:
        raise ConfigValidationError(f"{kind} must be relative: {value!r}")
    if any(part == ".." for part in posix_text.split("/")) or any(
        part == ".." for part in win.parts
    ):
        raise ConfigValidationError(f"{kind} must not contain '..': {value!r}")


def _normalise_env(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ConfigValidationError("'extra_env' must be a mapping of strings.")
    out: list[tuple[str, str]] = []
    for k, v in value.items():
        if isinstance(v, (Mapping, list, tuple, set)):
            raise ConfigValidationError(
                f"'extra_env' value for {k!r} must be a string, number or boolean, "
                f"not a {type(v).__name__}."
            )
        # YAML's null / true / false would otherwise reach the process as "None" / "True".
        if v is None:
            text = ""
        elif isinstance(v, bool):
            text = "true" if v else "false"
        else:
            text = str(v)
        out.append((str(k), text))
    return tuple(out)
