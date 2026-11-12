"""A stage's ``serve:`` (the app end-to-end tests need) and Cypress found by ``qasight init``."""

from __future__ import annotations

import json
import shlex
import socket
import sys
import urllib.request
from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import discover_and_load
from qasight.config.schema import StageServe
from qasight.config.sections import parse_serve
from qasight.engine.executor import run_stage
from qasight.engine.stage_server import ServeError, serving
from qasight.services import ci_templates, project_setup


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_server(port: int) -> str:
    return f"{shlex.quote(sys.executable)} -m http.server {port} --bind 127.0.0.1"


def _up(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except OSError:
        return False


def test_parse_serve_checks_its_keys() -> None:
    assert parse_serve(None, where="s") is None
    assert parse_serve({"command": "npm start", "url": "http://localhost:3000"}, where="s") == (
        StageServe(command="npm start", url="http://localhost:3000", timeout_s=120.0)
    )
    for raw, message in [
        ("npm start", "must be a mapping"),
        ({"url": "http://x"}, "serve.command"),
        ({"command": "x", "url": "localhost:3000"}, "serve.url"),
        ({"command": "x", "url": "http://x", "timeout_s": 0}, "serve.timeout_s"),
        ({"command": "x", "url": "http://x", "port": 1}, "unknown key 'port'"),
    ]:
        with pytest.raises(ConfigValidationError, match=message):
            parse_serve(raw, where="s")


def test_serving_waits_for_the_url_and_stops_the_app_after(tmp_path: Path) -> None:
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    serve = StageServe(command=_http_server(port), url=url, timeout_s=30)
    with serving(serve, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, log_path=tmp_path / "s.log"):
        assert _up(url)
    assert not _up(url)


def test_serving_says_why_the_app_did_not_start(tmp_path: Path) -> None:
    url = f"http://127.0.0.1:{_free_port()}/"
    exits = StageServe(command="echo no database; exit 3", url=url, timeout_s=30)
    with pytest.raises(ServeError, match=r"exited with code 3[\s\S]*no database"):
        with serving(exits, cwd=tmp_path, env={}, log_path=tmp_path / "a.log"):
            pass
    hangs = StageServe(command="sleep 30", url=url, timeout_s=1)
    with pytest.raises(ServeError, match="didn't answer within 1s"):
        with serving(hangs, cwd=tmp_path, env={}, log_path=tmp_path / "b.log"):
            pass


def _config(tmp_path: Path, serve: dict[str, object], check: str) -> Path:
    config = {
        "version": 1,
        "cycles": {
            "e2e": {
                "stages": [
                    {
                        "name": "browser",
                        "equipment": "command",
                        "args": [sys.executable, "-c", check],
                        "serve": serve,
                    }
                ]
            }
        },
    }
    path = tmp_path / "qasight.yaml"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_a_stage_runs_while_its_app_is_up(tmp_path: Path) -> None:
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    check = f"import urllib.request; urllib.request.urlopen({url!r}, timeout=5)"
    cfg = discover_and_load(
        config_path=_config(tmp_path, {"command": _http_server(port), "url": url}, check)
    )
    (stage,) = cfg.cycles["e2e"].stages
    result = run_stage(stage, plan_name="e2e", artifacts_root=tmp_path / "artifacts")
    assert result.returncode == 0, result.output_tail
    assert not _up(url)
    assert (tmp_path / "artifacts").rglob("serve.log")


def test_a_stage_whose_app_never_starts_fails_like_a_timeout(tmp_path: Path) -> None:
    url = f"http://127.0.0.1:{_free_port()}/"
    cfg = discover_and_load(
        config_path=_config(tmp_path, {"command": "exit 1", "url": url}, "print('never')")
    )
    (stage,) = cfg.cycles["e2e"].stages
    result = run_stage(stage, plan_name="e2e", artifacts_root=tmp_path / "artifacts")
    assert result.returncode == 124
    assert "the app didn't start" in (result.error or "")


# --- qasight init finds Cypress --------------------------------------------------------------


def _cypress_project(root: Path, *, base_url: bool = True) -> Path:
    pkg = {"scripts": {"dev": "vite", "test": "vitest"}, "devDependencies": {"cypress": "13"}}
    (root / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    body = "e2e: { baseUrl: 'http://localhost:5173' }" if base_url else "e2e: {}"
    (root / "cypress.config.ts").write_text(
        f"import {{ defineConfig }} from 'cypress'\nexport default defineConfig({{ {body} }})\n",
        encoding="utf-8",
    )
    return root


def test_init_writes_a_cypress_stage_that_starts_the_app(tmp_path: Path) -> None:
    facts = project_setup.detect_project(_cypress_project(tmp_path))
    (stack,) = [s for s in facts.stacks if s.key == "cypress"]
    assert stack.stage["serve"] == {"command": "npm run dev", "url": "http://localhost:5173"}
    (tmp_path / "qasight.yaml").write_text(project_setup.config_yaml(facts, cycle="web"))
    stage = discover_and_load(config_path=tmp_path / "qasight.yaml").cycles["web"].stages[0]
    assert stage.framework == "cypress"
    assert stage.tier == "e2e"
    assert stage.serve == StageServe(command="npm run dev", url="http://localhost:5173")

    command = ci_templates.ci_test_command(stack)
    assert command.startswith("mkdir -p test-results && (npm run dev > test-results/serve.log")
    assert "curl -s -o /dev/null http://localhost:5173" in command
    assert command.endswith("mochaFile=test-results/testo-cypress-[hash].xml,toConsole=false'")


def test_cypress_without_a_base_url_is_still_left_out_with_a_note(tmp_path: Path) -> None:
    facts = project_setup.detect_project(_cypress_project(tmp_path, base_url=False))
    assert all(s.key != "cypress" for s in facts.stacks)
    assert any("serve: {command" in note for note in facts.skipped)
