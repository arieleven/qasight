"""Selective cycle execution: detect changes under glob patterns (Git-first, snapshot fallback)."""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from qasight.config.schema import Plan, QASightConfig
from qasight.paths import safe_child_path

_GIT_TIMEOUT_S = 60.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TriggerResult:
    """Outcome of evaluating a cycle's ``trigger``."""

    stimulus: bool
    reason: str
    matched_paths: tuple[str, ...]
    mode: str  # "git" | "snapshot"
    persist_snapshot_after_run: bool  # True when evaluation used snapshot state


def evaluate_cycle_trigger(*, plan: Plan, cfg: QASightConfig) -> TriggerResult:
    """Return whether the cycle should run based on ``plan.trigger`` (must be non-None)."""
    trigger = plan.trigger
    assert trigger is not None  # caller guards
    source = cfg.source_path
    artifacts_root = cfg.defaults.artifacts_root.expanduser().resolve()
    if source is None:
        return TriggerResult(
            True,
            "no config source path; cannot evaluate trigger — activating",
            (),
            "snapshot",
            False,
        )
    anchor = source.parent.expanduser().resolve()
    patterns = trigger.paths

    git_error: str | None = None
    repo_root = _git_repo_root(anchor)
    if repo_root is not None:
        try:
            return _evaluate_git_trigger(
                plan_name=plan.name,
                anchor=anchor,
                repo_root=repo_root,
                patterns=patterns,
                since_ref=trigger.since_ref,
                artifacts_root=artifacts_root,
            )
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            git_error = str(exc) or type(exc).__name__
            # No traceback: this is an environment problem (shallow clone, unknown ref), not a bug.
            logger.warning(
                "git trigger evaluation failed for cycle %s (%s); falling back to snapshot mode",
                plan.name,
                git_error,
            )

    result = _evaluate_snapshot_trigger(
        anchor=anchor,
        plan_name=plan.name,
        patterns=patterns,
        artifacts_root=artifacts_root,
    )
    if git_error is not None:
        # Surface the fallback in the reason the CLI panel and `cycle_trigger` event already show.
        result = dataclasses.replace(
            result,
            reason=f"Git evaluation failed ({git_error}); snapshot fallback: {result.reason}",
        )
    return result


def persist_trigger_snapshot(
    *,
    cfg: QASightConfig,
    plan_name: str,
    anchor: Path,
    patterns: tuple[str, ...],
) -> None:
    """Write the current snapshot catalog after a successful snapshot-triggered run."""
    root = cfg.defaults.artifacts_root.expanduser().resolve()
    path = _snapshot_state_path(root, plan_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    catalog = _snapshot_catalog(anchor, patterns)
    payload = {rel: {"mtime_ns": mt, "size": sz} for rel, (mt, sz) in catalog.items()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _snapshot_state_path(artifacts_root: Path, plan_name: str) -> Path:
    state_root = artifacts_root / ".testo" / "trigger_state"
    return safe_child_path(state_root, f"{plan_name}.json", label="plan name")


def _git_repo_root(anchor: Path) -> Path | None:
    code, out, _ = _git_run(["git", "rev-parse", "--is-inside-work-tree"], anchor)
    if code != 0 or out.strip() != "true":
        return None
    code2, out2, _ = _git_run(["git", "rev-parse", "--show-toplevel"], anchor)
    if code2 != 0 or not out2.strip():
        return None
    return Path(out2.strip()).resolve()


def _git_run(argv: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603 — fixed argv
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _repo_paths_to_anchor_relative(
    repo_root: Path, anchor: Path, repo_rel_paths: Iterable[str]
) -> set[str]:
    anchor_r = anchor.resolve()
    repo_r = repo_root.resolve()
    out: set[str] = set()
    for rel in repo_rel_paths:
        rel = rel.strip().replace("\\", "/")
        if not rel:
            continue
        full = (repo_r / rel).resolve()
        try:
            under = full.relative_to(anchor_r)
        except ValueError:
            continue
        out.add(under.as_posix())
    return out


def _evaluate_git_trigger(
    *,
    plan_name: str,
    anchor: Path,
    repo_root: Path,
    patterns: tuple[str, ...],
    since_ref: str | None,
    artifacts_root: Path,
) -> TriggerResult:
    repo_r = repo_root.resolve()
    anchor_r = anchor.resolve()
    try:
        anchor_r.relative_to(repo_r)
    except ValueError:
        return _evaluate_snapshot_trigger(
            anchor=anchor,
            plan_name=plan_name,
            patterns=patterns,
            artifacts_root=artifacts_root,
        )

    if since_ref:
        diff_arg = f"{since_ref}...HEAD"
        code, out, err = _git_run(["git", "diff", "--name-only", diff_arg], repo_r)
    else:
        code, out, err = _git_run(["git", "diff", "--name-only", "HEAD"], repo_r)
    if code != 0:
        detail = err.strip().splitlines()[0] if err.strip() else f"exit {code}"
        raise RuntimeError(f"git diff failed: {detail}")

    paths: set[str] = {ln.strip() for ln in out.splitlines() if ln.strip()}
    code_u, out_u, _ = _git_run(["git", "ls-files", "--others", "--exclude-standard"], repo_r)
    if code_u == 0:
        paths |= {ln.strip() for ln in out_u.splitlines() if ln.strip()}

    under_anchor = _repo_paths_to_anchor_relative(repo_r, anchor_r, paths)
    matched = tuple(sorted(p for p in under_anchor if _path_matches_patterns(p, patterns)))
    if matched:
        return TriggerResult(
            True,
            f"Stimulus detected ({len(matched)} path(s) under trigger globs).",
            matched,
            "git",
            False,
        )
    if under_anchor:
        return TriggerResult(
            False,
            "Changes exist under the config tree but none match trigger.paths.",
            (),
            "git",
            False,
        )
    return TriggerResult(
        False,
        "No stimulus detected in targeted muscle groups.",
        (),
        "git",
        False,
    )


def _evaluate_snapshot_trigger(
    *,
    anchor: Path,
    plan_name: str,
    patterns: tuple[str, ...],
    artifacts_root: Path,
) -> TriggerResult:
    current = _snapshot_catalog(anchor, patterns)
    state_path = _snapshot_state_path(artifacts_root, plan_name)
    previous: dict[str, dict[str, int]] | None = None
    if state_path.is_file():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None

    if previous is None:
        return TriggerResult(
            True,
            "No prior trigger snapshot (non-Git or Git unavailable) — activating.",
            (),
            "snapshot",
            True,
        )

    prev_norm = {k: (int(v["mtime_ns"]), int(v["size"])) for k, v in previous.items()}
    if prev_norm == current:
        return TriggerResult(
            False,
            "No stimulus detected in targeted muscle groups.",
            (),
            "snapshot",
            False,
        )

    changed = _diff_catalog_keys(prev_norm, current)
    return TriggerResult(
        True,
        "Stimulus detected (file mtime/size changed vs last snapshot).",
        changed,
        "snapshot",
        True,
    )


def _snapshot_catalog(anchor: Path, patterns: tuple[str, ...]) -> dict[str, tuple[int, int]]:
    anchor_r = anchor.resolve()
    out: dict[str, tuple[int, int]] = {}
    for pat in patterns:
        try:
            for p in anchor.glob(pat):
                if not p.is_file():
                    continue
                pr = p.resolve()
                try:
                    rel = pr.relative_to(anchor_r).as_posix()
                except ValueError:
                    continue
                st = pr.stat()
                out[rel] = (int(st.st_mtime_ns), st.st_size)
        except OSError:
            continue
    return dict(sorted(out.items()))


def _path_matches_patterns(rel_posix: str, patterns: tuple[str, ...]) -> bool:
    pp = PurePosixPath(rel_posix)
    return any(pp.match(pat) for pat in patterns)


def _diff_catalog_keys(
    old: dict[str, tuple[int, int]],
    new: dict[str, tuple[int, int]],
    *,
    cap: int = 32,
) -> tuple[str, ...]:
    changed: list[str] = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            changed.append(k)
    return tuple(changed[:cap])


def path_matches_trigger_glob(rel_posix: str, pattern: str) -> bool:
    """Public helper for tests: whether ``rel_posix`` matches a single pathlib glob."""
    return PurePosixPath(rel_posix).match(pattern)
