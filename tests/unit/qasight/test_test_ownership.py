"""Test owners from CODEOWNERS and prefilled issue links (qasight/services/test_ownership.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from qasight.services.ci_provenance import ci_repository, github_repository_of
from qasight.services.test_ownership import issue_url, owners_for, source_path


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("tests.api.test_orders.test_create_order", "tests/api/test_orders.py"),
        ("tests.test_x.TestClass.test_y", "tests/test_x.py"),
        ("tests/test_a.py::test_x", "tests/test_a.py"),
        ("./features/login.feature:12", "features/login.feature"),
        ("", None),
        ("Login works", None),
    ],
)
def test_source_path_from_test_names(full_name: str, expected: str | None) -> None:
    assert source_path(full_name) == expected


def test_owners_follow_github_rules_last_match_wins(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        "# comment\n"
        "*            @org/everyone\n"
        "/tests/api/  @org/api   # inline comment\n"
        "*.feature    @org/qa\n"
        "tests/api/test_orders.py @dana\n",
        encoding="utf-8",
    )
    assert owners_for(tmp_path, "tests.api.test_orders.test_create") == ("@dana",)
    assert owners_for(tmp_path, "tests.api.test_users.test_list") == ("@org/api",)
    assert owners_for(tmp_path, "features/login.feature:3") == ("@org/qa",)
    assert owners_for(tmp_path, "tests.unit.test_math.test_add") == ("@org/everyone",)
    # Anchored: /tests/api/ does not match a nested tests/api/ elsewhere.
    assert owners_for(tmp_path, "pkg/tests/api/test_x.py::t") == ("@org/everyone",)


def test_no_owners_without_codeowners_or_a_path(tmp_path: Path) -> None:
    assert owners_for(tmp_path, "tests.api.test_orders.test_create") == ()
    assert owners_for(None, "tests.api.test_orders.test_create") == ()
    (tmp_path / "CODEOWNERS").write_text("* @a\n", encoding="utf-8")
    assert owners_for(tmp_path, "Login works") == ()


def test_issue_url_prefills_github_new_issue_form() -> None:
    url = issue_url(
        "acme/shop",
        cycle="smoke",
        test_name="test_login",
        full_name="tests.ui.test_login",
        message="TimeoutError: page.click",
        owners=("@dana",),
    )
    assert url is not None
    parsed = urlparse(url)
    assert (parsed.netloc, parsed.path) == ("github.com", "/acme/shop/issues/new")
    query = parse_qs(parsed.query)
    assert query["title"] == ["Flaky test: test_login"]
    body = query["body"][0]
    assert "`tests.ui.test_login` in cycle `smoke`" in body
    assert "TimeoutError: page.click" in body
    assert "@dana" in body


@pytest.mark.parametrize("repository", [None, "", "not a repo", "https://evil.example/x"])
def test_no_issue_url_without_a_github_repository(repository: str | None) -> None:
    assert issue_url(repository, cycle="smoke", test_name="t") is None


def test_ci_repository_from_provider_variables() -> None:
    assert ci_repository({"GITHUB_REPOSITORY": "acme/shop"}) == "acme/shop"
    assert ci_repository({"CI_PROJECT_PATH": "group/proj"}) == "group/proj"
    assert ci_repository({}) is None


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:acme/shop.git", "acme/shop"),
        ("https://github.com/acme/shop", "acme/shop"),
        ("https://gitlab.com/acme/shop.git", None),
    ],
)
def test_github_repository_of_a_checkout(tmp_path: Path, remote: str, expected: str | None) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", remote], check=True)
    assert github_repository_of(tmp_path) == expected
    assert github_repository_of(None) is None
