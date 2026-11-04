"""``qasight users``: manage team server accounts in the run-history database."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.accounts import AccountStore
from qasight.cli.app import app
from qasight.repository.db_config import reset_engine_cache

_PASSWORD = "a long enough password"


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    reset_engine_cache()
    yield
    reset_engine_cache()


def _users(*args: str, password: str | None = None):  # noqa: ANN202
    return CliRunner().invoke(app, ["users", *args], input=password)


def test_add_list_set_role_and_remove(db: None) -> None:
    added = _users("add", "alice", "--role", "runner", "--password-stdin", password=_PASSWORD)
    assert added.exit_code == 0, added.output
    assert "added alice (runner)" in added.output
    assert AccountStore().authenticate("alice", _PASSWORD) is not None

    listed = _users("list")
    assert "alice" in listed.output and "runner" in listed.output

    assert _users("set-role", "alice", "admin").exit_code == 0
    assert AccountStore().get("alice").role == "admin"  # type: ignore[union-attr]

    assert _users("remove", "alice").exit_code == 0
    assert AccountStore().get("alice") is None
    assert "No accounts yet" in _users("list").output


def test_prompted_password_is_asked_twice(db: None) -> None:
    result = _users("add", "bob", password=f"{_PASSWORD}\n{_PASSWORD}\n")
    assert result.exit_code == 0, result.output
    assert AccountStore().authenticate("bob", _PASSWORD) is not None


def test_reset_password_and_sign_out_end_sessions(db: None) -> None:
    _users("add", "carol", "--password-stdin", password=_PASSWORD)
    version = AccountStore().get("carol").session_version  # type: ignore[union-attr]

    assert (
        _users("reset-password", "carol", "--password-stdin", password="another long one").exit_code
        == 0
    )
    assert AccountStore().authenticate("carol", "another long one") is not None
    assert _users("sign-out", "carol").exit_code == 0
    assert AccountStore().get("carol").session_version == version + 2  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "args",
    [
        ["add", "dave", "--password-stdin"],  # password too short
        ["add", "bad name", "--password-stdin"],
        ["set-role", "nobody", "admin"],
        ["remove", "nobody"],
        ["sign-out", "nobody"],
        ["reset-password", "nobody", "--password-stdin"],
    ],
)
def test_bad_input_exits_2(db: None, args: list[str]) -> None:
    password = "short" if args[1] == "dave" else _PASSWORD
    result = _users(*args, password=password)
    assert result.exit_code == 2, result.output


def test_unknown_role_exits_2(db: None) -> None:
    _users("add", "erin", "--password-stdin", password=_PASSWORD)
    assert _users("set-role", "erin", "root").exit_code == 2


@pytest.mark.parametrize(
    "args",
    [
        ["add", "bad name"],
        ["add", "frank", "--role", "root"],
        ["reset-password", "nobody"],
    ],
)
def test_bad_name_or_role_is_refused_before_the_password_prompt(db: None, args: list[str]) -> None:
    result = _users(*args, password="")  # no password typed at all
    assert result.exit_code == 2, result.output
    assert "Password" not in result.output


def test_existing_user_is_refused_before_the_password_prompt(db: None) -> None:
    _users("add", "gina", "--password-stdin", password=_PASSWORD)
    result = _users("add", "gina", password="")
    assert result.exit_code == 2
    assert "already exists" in result.output
    assert "Password" not in result.output
