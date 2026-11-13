from __future__ import annotations

import subprocess
from pathlib import Path

from qasight.services.ci_provenance import (
    CIProvenance,
    detect_ci_environment,
    detect_ci_provenance,
    detect_git_checkout,
)


def test_detect_ci_provenance_github() -> None:
    pv = detect_ci_provenance(
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_RUN_ID": "123",
            "GITHUB_JOB": "test",
            "GITHUB_SHA": "abc123",
            "GITHUB_REF_NAME": "main",
        }
    )
    assert pv == CIProvenance(
        ci_provider="github",
        ci_pipeline_id="123",
        ci_job_id="test",
        ci_commit_sha="abc123",
        ci_ref_name="main",
    )


def test_detect_ci_provenance_gitlab() -> None:
    pv = detect_ci_provenance(
        {
            "GITLAB_CI": "true",
            "CI_PIPELINE_ID": "42",
            "CI_JOB_ID": "99",
            "CI_COMMIT_SHA": "def456",
            "CI_COMMIT_REF_NAME": "release",
        }
    )
    assert pv == CIProvenance(
        ci_provider="gitlab",
        ci_pipeline_id="42",
        ci_job_id="99",
        ci_commit_sha="def456",
        ci_ref_name="release",
    )


def test_detect_ci_provenance_none_when_unknown_environment() -> None:
    assert detect_ci_provenance({"CI": "true"}) == CIProvenance(ci_provider="generic")


def test_to_metadata_omits_missing_optional_fields() -> None:
    payload = CIProvenance(ci_provider="github", ci_pipeline_id="123").to_metadata()
    assert payload == {"ci_provider": "github", "ci_pipeline_id": "123"}


def test_detect_ci_provenance_jenkins() -> None:
    pv = detect_ci_provenance(
        {
            "JENKINS_URL": "https://jenkins.local",
            "BUILD_NUMBER": "11",
            "BUILD_ID": "2026-05-02_01-02-03",
            "GIT_COMMIT": "cafedead",
            "BRANCH_NAME": "main",
        }
    )
    assert pv == CIProvenance(
        ci_provider="jenkins",
        ci_pipeline_id="2026-05-02_01-02-03",
        ci_job_id="11",
        ci_commit_sha="cafedead",
        ci_ref_name="main",
    )


def test_detect_ci_environment_true_for_known_provider() -> None:
    assert detect_ci_environment({"CIRCLECI": "true"}) is True


def test_detect_ci_environment_false_for_local() -> None:
    assert detect_ci_environment({"SHELL": "/bin/zsh"}) is False


def test_github_pull_request_records_the_branch_and_its_target() -> None:
    pv = detect_ci_provenance(
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF_NAME": "167/merge",
            "GITHUB_HEAD_REF": "feature/login",
            "GITHUB_BASE_REF": "main",
        }
    )
    assert pv is not None
    assert pv.ci_ref_name == "feature/login"
    assert pv.to_metadata()["ci_base_ref"] == "main"


def test_push_builds_have_no_base_ref() -> None:
    pv = detect_ci_provenance(
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF_NAME": "main",
            "GITHUB_HEAD_REF": "",
            "GITHUB_BASE_REF": "",
        }
    )
    assert pv is not None
    assert pv.ci_ref_name == "main"
    assert "ci_base_ref" not in pv.to_metadata()


def test_azure_and_jenkins_branch_refs_are_short_names() -> None:
    azure = detect_ci_provenance(
        {"TF_BUILD": "True", "SYSTEM_PULLREQUEST_TARGETBRANCH": "refs/heads/main"}
    )
    jenkins = detect_ci_provenance(
        {"JENKINS_URL": "http://ci", "GIT_BRANCH": "origin/feature", "CHANGE_TARGET": "main"}
    )
    assert azure is not None and azure.ci_base_ref == "main"
    assert jenkins is not None and (jenkins.ci_ref_name, jenkins.ci_base_ref) == ("feature", "main")


def test_detect_git_checkout_reads_branch_and_commit(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    found = detect_git_checkout(tmp_path)
    assert found["git_branch"] == "trunk"
    assert len(found["git_commit"]) == 40


def test_detect_git_checkout_outside_a_repo(tmp_path: Path) -> None:
    assert detect_git_checkout(tmp_path) == {}
    assert detect_git_checkout(None) == {}


def test_ci_tag_reads_each_providers_tag() -> None:
    from qasight.services.ci_provenance import ci_tag

    gh = {"GITHUB_ACTIONS": "true", "GITHUB_REF_NAME": "v1.2.0"}
    assert ci_tag({**gh, "GITHUB_REF_TYPE": "tag"}) == "v1.2.0"
    assert ci_tag({**gh, "GITHUB_REF_TYPE": "branch"}) is None
    assert ci_tag({"GITLAB_CI": "true", "CI_COMMIT_TAG": "2.0"}) == "2.0"
    assert ci_tag({"CIRCLECI": "true", "CIRCLE_TAG": "3.0"}) == "3.0"
    assert ci_tag({"TF_BUILD": "True", "BUILD_SOURCEBRANCH": "refs/tags/4.0"}) == "4.0"
    assert ci_tag({"TF_BUILD": "True", "BUILD_SOURCEBRANCH": "refs/heads/main"}) is None
    assert ci_tag({"GITLAB_CI": "true", "CI_COMMIT_TAG": "2.0", "TESTO_AUTO_METADATA": "0"}) is None


def test_ci_os_only_in_ci() -> None:
    from qasight.services.ci_provenance import ci_os

    assert ci_os({"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS"}) == "macos"
    assert ci_os({"TF_BUILD": "True", "AGENT_OS": "Windows_NT"}) == "windows"
    assert ci_os({"RUNNER_OS": "Linux"}) is None  # not in CI
    assert ci_os({"CI": "true", "RUNNER_OS": "Linux", "TESTO_AUTO_METADATA": "off"}) is None
