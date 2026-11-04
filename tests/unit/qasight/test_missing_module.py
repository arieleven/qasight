"""A missing dependency is one line with the right ``pip install`` (I2), and ``qasight ui``
offers to install the api extra (I3)."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest
import typer

from qasight.cli import app as app_mod
from qasight.cli.commands import ui_cmd
from qasight.cli.missing_module import missing_module_hint

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.mark.parametrize(
    ("module", "extra"),
    [("fastapi", "api"), ("multipart", "api"), ("mcp", "mcp"), ("opentelemetry.sdk", "otel")],
)
def test_an_extras_module_names_the_extra(module: str, extra: str) -> None:
    hint = missing_module_hint(module)
    assert hint is not None and f"pip install 'qasight[{extra}]'" in hint


def test_a_core_dependency_suggests_a_reinstall() -> None:
    hint = missing_module_hint("yaml")
    assert hint is not None and "pip install --force-reinstall qasight" in hint


def test_a_module_that_isnt_ours_gets_no_hint() -> None:
    assert missing_module_hint("numpy_never_a_dep") is None
    assert missing_module_hint(None) is None


def test_main_prints_the_hint_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(**_: Any) -> None:
        raise ModuleNotFoundError("No module named 'mcp'", name="mcp")

    monkeypatch.setattr(app_mod, "app", boom)
    assert app_mod.main(["mcp"]) == 2
    assert "pip install 'qasight[mcp]'" in capsys.readouterr().err


def test_main_reraises_a_module_that_isnt_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_: Any) -> None:
        raise ModuleNotFoundError("nope", name="numpy_never_a_dep")

    monkeypatch.setattr(app_mod, "app", boom)
    with pytest.raises(ModuleNotFoundError):
        app_mod.main(["x"])


class _Tty:
    def isatty(self) -> bool:
        return True


def test_ui_offers_to_install_the_api_extra_in_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"installed": False}
    monkeypatch.setattr(ui_cmd, "_has_api_extra", lambda: state["installed"])
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stdout", _Tty())
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        state["installed"] = True
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert ui_cmd._api_extra_ready() is True
    assert calls[0][:4] == [sys.executable, "-m", "pip", "install"]
    assert calls[0][4].startswith("qasight[api]==")


def test_ui_without_a_terminal_only_says_what_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ui_cmd, "_has_api_extra", lambda: False)

    def no_run(*_: Any, **__: Any) -> None:
        raise AssertionError("must not install without asking")

    monkeypatch.setattr(subprocess, "run", no_run)
    assert ui_cmd._api_extra_ready() is False
