from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CIProvenance:
    ci_provider: str
    ci_pipeline_id: str | None = None
    ci_job_id: str | None = None
    ci_commit_sha: str | None = None
    ci_ref_name: str | None = None
    # The branch a pull/merge request will merge into; unset for branch builds.
    ci_base_ref: str | None = None

    def to_metadata(self) -> dict[str, str]:
        payload: dict[str, str] = {"ci_provider": self.ci_provider}
        if self.ci_pipeline_id:
            payload["ci_pipeline_id"] = self.ci_pipeline_id
        if self.ci_job_id:
            payload["ci_job_id"] = self.ci_job_id
        if self.ci_commit_sha:
            payload["ci_commit_sha"] = self.ci_commit_sha
        if self.ci_ref_name:
            payload["ci_ref_name"] = self.ci_ref_name
        if self.ci_base_ref:
            payload["ci_base_ref"] = self.ci_base_ref
        return payload


def _branch_name(ref: str | None) -> str | None:
    """``refs/heads/main`` (Azure, Jenkins ``origin/main``) as ``main``."""
    if not ref:
        return None
    for prefix in ("refs/heads/", "origin/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def detect_ci_provenance(env: Mapping[str, str] | None = None) -> CIProvenance | None:
    source = env or {}
    if str(source.get("GITHUB_ACTIONS", "")).lower() == "true":
        return CIProvenance(
            ci_provider="github",
            ci_pipeline_id=source.get("GITHUB_RUN_ID"),
            ci_job_id=source.get("GITHUB_JOB"),
            ci_commit_sha=source.get("GITHUB_SHA"),
            # On pull_request events GITHUB_REF_NAME is "<number>/merge"; HEAD_REF is the branch.
            ci_ref_name=source.get("GITHUB_HEAD_REF") or source.get("GITHUB_REF_NAME"),
            ci_base_ref=source.get("GITHUB_BASE_REF") or None,
        )
    if str(source.get("GITLAB_CI", "")).lower() == "true":
        return CIProvenance(
            ci_provider="gitlab",
            ci_pipeline_id=source.get("CI_PIPELINE_ID"),
            ci_job_id=source.get("CI_JOB_ID"),
            ci_commit_sha=source.get("CI_COMMIT_SHA"),
            ci_ref_name=source.get("CI_COMMIT_REF_NAME"),
            ci_base_ref=source.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME") or None,
        )
    if str(source.get("BUILDKITE", "")).lower() == "true":
        return CIProvenance(
            ci_provider="buildkite",
            ci_pipeline_id=source.get("BUILDKITE_BUILD_ID"),
            ci_job_id=source.get("BUILDKITE_JOB_ID"),
            ci_commit_sha=source.get("BUILDKITE_COMMIT"),
            ci_ref_name=source.get("BUILDKITE_BRANCH"),
            ci_base_ref=source.get("BUILDKITE_PULL_REQUEST_BASE_BRANCH") or None,
        )
    if str(source.get("CIRCLECI", "")).lower() == "true":
        return CIProvenance(
            ci_provider="circleci",
            ci_pipeline_id=source.get("CIRCLE_WORKFLOW_ID"),
            ci_job_id=source.get("CIRCLE_BUILD_NUM"),
            ci_commit_sha=source.get("CIRCLE_SHA1"),
            ci_ref_name=source.get("CIRCLE_BRANCH"),
        )
    if source.get("JENKINS_URL"):
        return CIProvenance(
            ci_provider="jenkins",
            ci_pipeline_id=source.get("BUILD_TAG") or source.get("BUILD_ID"),
            ci_job_id=source.get("BUILD_NUMBER"),
            ci_commit_sha=source.get("GIT_COMMIT"),
            ci_ref_name=_branch_name(source.get("GIT_BRANCH") or source.get("BRANCH_NAME")),
            ci_base_ref=source.get("CHANGE_TARGET") or None,
        )
    if source.get("TF_BUILD") == "True" or source.get("TF_BUILD") == "true":
        return CIProvenance(
            ci_provider="azure_pipelines",
            ci_pipeline_id=source.get("BUILD_BUILDID"),
            ci_job_id=source.get("SYSTEM_JOBID"),
            ci_commit_sha=source.get("BUILD_SOURCEVERSION"),
            ci_ref_name=source.get("BUILD_SOURCEBRANCHNAME"),
            ci_base_ref=_branch_name(source.get("SYSTEM_PULLREQUEST_TARGETBRANCH")),
        )
    if str(source.get("CI", "")).lower() == "true":
        return CIProvenance(ci_provider="generic")
    return None


def detect_git_checkout(repo: Path | None) -> dict[str, str]:
    """Branch and commit of a local checkout, for runs outside CI (``{}`` when not a git repo)."""
    if repo is None:
        return {}
    found: dict[str, str] = {}
    for key, args in (
        ("git_branch", ["rev-parse", "--abbrev-ref", "HEAD"]),
        ("git_commit", ["rev-parse", "HEAD"]),
    ):
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return found
        value = out.stdout.strip()
        # A detached HEAD reports "HEAD" as its branch: no branch to compare with.
        if out.returncode == 0 and value and value != "HEAD":
            found[key] = value
    return found


_GITHUB_REMOTE = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def ci_repository(env: Mapping[str, str]) -> str | None:
    """``owner/name`` of the repository this CI job built, from the provider's variables."""
    return (
        env.get("GITHUB_REPOSITORY")
        or env.get("CI_PROJECT_PATH")
        or env.get("BUILDKITE_REPO")
        or env.get("CIRCLE_PROJECT_REPONAME")
        or None
    )


def github_repository_of(repo: Path | None) -> str | None:
    """``owner/name`` from the checkout's ``origin`` remote, when it is on github.com."""
    if repo is None:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _GITHUB_REMOTE.search(out.stdout.strip()) if out.returncode == 0 else None
    return match.group(1) if match else None


def detect_ci_environment(env: Mapping[str, str] | None = None) -> bool:
    return detect_ci_provenance(env) is not None
