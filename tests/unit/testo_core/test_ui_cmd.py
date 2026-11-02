"""``testo ui`` (testo_core/cli/commands/ui_cmd.py) and the bundled UI lookup (testo_core/ui_bundle.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from testo_api import ui as ui_mod
from testo_core import ui_bundle
from testo_core.cli.app import app
from testo_core.cli.commands import ui_cmd


def _build(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return path


def test_ui_dir_prefers_the_env_then_the_bundled_build(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    bundled = _build(tmp_path / "bundled")
    custom = _build(tmp_path / "custom")
    monkeypatch.setattr(ui_bundle, "bundled_ui_dir", lambda: bundled)

    monkeypatch.delenv(ui_mod.UI_DIR_ENV, raising=False)
    assert ui_mod.ui_dir() == bundled.resolve()

    monkeypatch.setenv(ui_mod.UI_DIR_ENV, str(custom))
    assert ui_mod.ui_dir() == custom.resolve()

    # A TESTO_UI_DIR that isn't a build means "no UI", not "fall back".
    monkeypatch.setenv(ui_mod.UI_DIR_ENV, str(tmp_path / "missing"))
    assert ui_mod.ui_dir() is None


def test_ui_without_a_build_explains_how_to_get_one(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.setattr(ui_bundle, "bundled_ui_dir", lambda: tmp_path / "none")
    monkeypatch.delenv(ui_mod.UI_DIR_ENV, raising=False)
    result = CliRunner().invoke(app, ["ui", "--no-open"])
    assert result.exit_code == 2
    assert "No built UI found" in result.output


def test_ui_serves_locally_and_lets_only_its_own_origin_write(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    bundled = _build(tmp_path / "bundled")
    monkeypatch.setattr(ui_bundle, "bundled_ui_dir", lambda: bundled)
    monkeypatch.delenv(ui_mod.UI_DIR_ENV, raising=False)
    monkeypatch.setenv("TESTO_API_HOST", "0.0.0.0")
    monkeypatch.delenv("TESTO_CORS_ORIGINS", raising=False)
    seen: dict[str, str] = {}

    def fake_run(argv: list[str]) -> int:  # noqa: ARG001
        import os

        seen.update(
            host=os.environ["TESTO_API_HOST"],
            port=os.environ["TESTO_API_PORT"],
            cors=os.environ["TESTO_CORS_ORIGINS"],
        )
        return 0

    monkeypatch.setattr("testo_api.main.run", fake_run)
    result = CliRunner().invoke(app, ["ui", "--port", "8123", "--no-open"])

    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:8123/" in result.output
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == "8123"
    origins = seen["cors"].split(",")
    assert "http://127.0.0.1:8123" in origins and "http://localhost:8123" in origins
    # The Vite dev servers stay allowed; nothing wider is added.
    assert "http://localhost:5173" in origins
    assert "*" not in origins


@pytest.mark.parametrize(
    "existing", ["https://ci.example.com", "https://ci.example.com,http://127.0.0.1:9000"]
)
def test_ui_keeps_origins_already_configured(monkeypatch, existing: str) -> None:  # noqa: ANN001
    monkeypatch.setenv("TESTO_CORS_ORIGINS", existing)
    merged = ui_cmd._merged_cors(9000).split(",")
    assert merged[0] == "https://ci.example.com"
    assert merged.count("http://127.0.0.1:9000") == 1
