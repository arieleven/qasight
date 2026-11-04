"""The engine cache closes what it forgets (no pooled connections left open)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event, text

from qasight.repository.db_config import engine_kwargs, get_engine, reset_engine_cache


def test_reset_disposes_every_cached_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_engine_cache()
    closed: list[object] = []
    engines = []
    for name in ("a", "b"):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / name}.db")
        engine = get_engine()
        assert get_engine() is engine  # cached per URL
        event.listen(engine.pool, "close", lambda conn, rec: closed.append(conn))
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engines.append(engine)

    reset_engine_cache()

    assert len(closed) == 2  # each engine's pooled connection was closed
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'a'}.db")
    assert get_engine() is not engines[0]
    reset_engine_cache()


def test_engine_kwargs_by_dialect() -> None:
    assert engine_kwargs("sqlite:///x.db") == {"connect_args": {"check_same_thread": False}}
    assert engine_kwargs("postgresql+psycopg://u@h/db") == {"pool_pre_ping": True}
