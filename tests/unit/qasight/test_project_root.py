"""The project folder is found upward from a subfolder (qasight/project_root.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.project_root import find_project_root

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def test_finds_the_folder_with_the_history_from_a_subfolder(tmp_path: Path) -> None:
    (tmp_path / "testo_history.db").write_bytes(b"")
    sub = tmp_path / "tests" / "deep"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == tmp_path.resolve()


@pytest.mark.parametrize("marker", ["qasight.yaml", ".testo"])
def test_config_or_data_folder_marks_the_project(tmp_path: Path, marker: str) -> None:
    target = tmp_path / marker
    target.mkdir() if marker.startswith(".") else target.write_text("version: 1\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    assert find_project_root(sub) == tmp_path.resolve()


def test_pyproject_counts_only_with_a_qasight_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    sub = tmp_path / "src"
    sub.mkdir()
    assert find_project_root(sub) == sub.resolve()
    (tmp_path / "pyproject.toml").write_text("[tool.qasight]\nversion = 1\n")
    assert find_project_root(sub) == tmp_path.resolve()


def test_the_walk_stops_at_the_repository_root(tmp_path: Path) -> None:
    (tmp_path / "qasight.yaml").write_text("version: 1\n")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src"
    sub.mkdir()
    assert find_project_root(sub) == sub.resolve()


def test_nothing_found_means_the_current_folder(tmp_path: Path) -> None:
    sub = tmp_path / "a"
    sub.mkdir()
    (tmp_path / ".git").mkdir()
    assert find_project_root(sub) == sub.resolve()


def test_default_history_database_follows_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qasight.repository.db_config import resolve_database_url

    (tmp_path / ".git").mkdir()
    (tmp_path / "testo_history.db").write_bytes(b"")
    sub = tmp_path / "tests"
    sub.mkdir()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for var in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(sub)
    assert (
        resolve_database_url()
        == f"sqlite:///{(tmp_path.resolve() / 'testo_history.db').as_posix()}"
    )
    monkeypatch.chdir(tmp_path)
    assert resolve_database_url() == "sqlite:///./testo_history.db"
