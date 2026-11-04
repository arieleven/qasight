"""Tests for local report server helpers."""

from __future__ import annotations

import socket
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from qasight.reporting import server
from qasight.reporting.server import find_free_port, resolve_serve_port


def test_find_free_port_returns_ephemeral() -> None:
    p = find_free_port()
    assert isinstance(p, int)
    assert 1 <= p <= 65535


def test_resolve_serve_port_zero_means_ephemeral() -> None:
    p = resolve_serve_port("127.0.0.1", 0)
    assert p != 0


def test_resolve_serve_port_falls_back_when_preferred_busy() -> None:
    host = "127.0.0.1"
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind((host, 0))
    busy = int(holder.getsockname()[1])
    holder.listen(1)
    try:
        chosen = resolve_serve_port(host, busy)
        assert chosen != busy
    finally:
        holder.close()


class _FakeProc:
    def __init__(self, waits: list[object]) -> None:
        self._waits = waits
        self.signals: list[int] = []
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        outcome = self._waits.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return int(outcome)  # type: ignore[call-overload]

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def kill(self) -> None:
        self.killed = True


def test_open_generated_report_needs_a_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        server.open_generated_report(report_dir=tmp_path / "missing")


def test_open_generated_report_returns_127_without_allure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server.shutil, "which", lambda _name: None)

    assert server.open_generated_report(report_dir=tmp_path) == 127


def test_open_generated_report_runs_allure_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv: list[list[str]] = []

    def fake_popen(cmd: list[str], **_kw: object) -> _FakeProc:
        argv.append(cmd)
        return _FakeProc([0])

    monkeypatch.setattr(server.shutil, "which", lambda _name: "/usr/bin/allure")
    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)

    assert server.open_generated_report(report_dir=tmp_path, host="0.0.0.0", port=9100) == 0
    assert argv == [
        ["allure", "open", str(tmp_path.resolve()), "--host", "0.0.0.0", "--port", "9100"]
    ]


def test_ctrl_c_stops_allure_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc([KeyboardInterrupt(), 0])
    monkeypatch.setattr(server.shutil, "which", lambda _name: "/usr/bin/allure")
    monkeypatch.setattr(server.subprocess, "Popen", lambda *_a, **_k: proc)

    assert server.open_generated_report(report_dir=tmp_path) == 0
    assert proc.signals == [server.signal.SIGTERM]
    assert proc.killed is False


def test_allure_open_that_ignores_sigterm_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _FakeProc([KeyboardInterrupt(), subprocess.TimeoutExpired("allure", 5.0)])
    monkeypatch.setattr(server.shutil, "which", lambda _name: "/usr/bin/allure")
    monkeypatch.setattr(server.subprocess, "Popen", lambda *_a, **_k: proc)

    assert server.open_generated_report(report_dir=tmp_path) == 130
    assert proc.killed is True


def test_serve_report_needs_a_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        server.serve_report(report_dir=tmp_path / "missing")


def test_serve_report_prefers_allure_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_open(**kwargs: object) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(server.shutil, "which", lambda _name: "/usr/bin/allure")
    monkeypatch.setattr(server, "resolve_serve_port", lambda _host, port: port + 1)
    monkeypatch.setattr(server, "open_generated_report", fake_open)

    assert server.serve_report(report_dir=tmp_path, port=8080) == 0
    assert seen == {"report_dir": tmp_path.resolve(), "host": "127.0.0.1", "port": 8081}


def test_serve_report_falls_back_to_the_stdlib_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake_stdlib(**kwargs: object) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(server.shutil, "which", lambda _name: None)
    monkeypatch.setattr(server, "resolve_serve_port", lambda _host, port: port)
    monkeypatch.setattr(server, "_serve_with_stdlib", fake_stdlib)

    assert server.serve_report(report_dir=tmp_path, port=8090) == 0
    assert seen == {"report_dir": tmp_path.resolve(), "port": 8090}


def test_stdlib_server_serves_files_until_ctrl_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "index.html").write_text("<h1>report</h1>", encoding="utf-8")
    port = server.find_free_port()
    fetched: list[bytes] = []

    class _ThreadThatFetchesThenInterrupts(threading.Thread):
        def join(self, timeout: float | None = None) -> None:  # noqa: ARG002
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=5) as resp:
                fetched.append(resp.read())
            raise KeyboardInterrupt

    monkeypatch.setattr(server.threading, "Thread", _ThreadThatFetchesThenInterrupts)

    assert server._serve_with_stdlib(report_dir=tmp_path, port=port) == 0
    assert fetched == [b"<h1>report</h1>"]
    assert f"http://127.0.0.1:{port}/" in capsys.readouterr().out


def test_background_server_serves_the_report_dir(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")

    with server.background_server(report_dir=tmp_path) as port:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/a.txt", timeout=5) as resp:
            assert resp.read() == b"hello"
