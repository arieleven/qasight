"""Tests for qasight ``database.url`` and :func:`resolve_database_url`."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.database_section import (
    build_postgresql_url,
    database_url_from_discovered_config,
    extract_database_url_from_mapping,
    merge_database_url_yaml,
)
from qasight.repository.db_config import reset_engine_cache, resolve_database_url


def test_extract_database_url_from_mapping() -> None:
    assert extract_database_url_from_mapping({}) is None
    assert extract_database_url_from_mapping({"database": "x"}) is None
    assert (
        extract_database_url_from_mapping({"database": {"url": "  sqlite:///x.db  "}})
        == "sqlite:///x.db"
    )


def test_database_url_from_discovered_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert database_url_from_discovered_config() is None

    yml = tmp_path / "qasight.yaml"
    yml.write_text(
        "version: 1\ndefaults: {target_repo: ., artifacts_root: artifacts}\n"
        "database:\n  url: sqlite:///from_yaml.db\n"
        "cycles:\n  c:\n    stages:\n      - {name: s, equipment: pytest, args: ['-q']}\n",
        encoding="utf-8",
    )
    assert database_url_from_discovered_config() == "sqlite:///from_yaml.db"


def test_resolve_database_url_prefers_env_over_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "qasight.yaml").write_text(
        "version: 1\ndefaults: {target_repo: ., artifacts_root: artifacts}\n"
        "database:\n  url: sqlite:///from_yaml.db\n"
        "cycles:\n  c:\n    stages:\n      - {name: s, equipment: pytest, args: ['-q']}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from_env.db")
    reset_engine_cache()
    try:
        assert resolve_database_url() == "sqlite:///from_env.db"
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        reset_engine_cache()


def test_resolve_database_url_uses_file_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    (tmp_path / "qasight.yaml").write_text(
        "version: 1\ndefaults: {target_repo: ., artifacts_root: artifacts}\n"
        "database:\n  url: sqlite:///from_yaml.db\n"
        "cycles:\n  c:\n    stages:\n      - {name: s, equipment: pytest, args: ['-q']}\n",
        encoding="utf-8",
    )
    reset_engine_cache()
    try:
        assert resolve_database_url() == "sqlite:///from_yaml.db"
    finally:
        reset_engine_cache()


def test_build_postgresql_url_encodes_special_chars() -> None:
    u = build_postgresql_url(
        host="h",
        port=5432,
        username="u@x",
        password="p/w",
        database="db",
        schema="myschema",
    )
    assert "postgresql+psycopg://" in u
    assert "u%40x" in u
    assert "p%2Fw" in u
    assert "search_path%3Dmyschema" in u


def test_merge_database_url_yaml_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "qasight.yaml"
    path.write_text(
        "version: 1\ndefaults: {target_repo: ., artifacts_root: artifacts}\n"
        "cycles:\n  c:\n    stages:\n      - {name: s, equipment: pytest, args: ['-q']}\n",
        encoding="utf-8",
    )
    merge_database_url_yaml(path=path, url="sqlite:///merged.db")
    assert "merged.db" in path.read_text(encoding="utf-8")
    assert database_url_from_discovered_config(cwd=tmp_path) == "sqlite:///merged.db"
