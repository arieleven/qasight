"""``qasight mcp install`` (qasight/services/mcp_install.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.services import mcp_install

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "shop"
    root.mkdir()
    (root / "qasight.yaml").write_text("cycles: {}\n")
    return root


def test_claude_code_gets_a_project_mcp_json_relative_to_the_project(project: Path) -> None:
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))

    result = CliRunner().invoke(app, ["mcp", "install", "--dir", str(project)])

    assert result.exit_code == 0, result.output
    assert "Added qasight" in result.output
    servers = json.loads((project / ".mcp.json").read_text())["mcpServers"]
    assert servers["other"] == {"command": "x"}
    assert servers["qasight"] == {
        "command": "qasight",
        "args": ["mcp", "--config", "qasight.yaml"],
    }

    again = CliRunner().invoke(app, ["mcp", "install", "--dir", str(project), "--allow-writes"])
    assert "Updated qasight" in again.output
    servers = json.loads((project / ".mcp.json").read_text())["mcpServers"]
    assert servers["qasight"]["args"][-1] == "--allow-writes"


def test_vscode_uses_servers_with_a_stdio_type(project: Path) -> None:
    CliRunner().invoke(app, ["mcp", "install", "--dir", str(project), "--client", "vscode"])

    block = json.loads((project / ".vscode" / "mcp.json").read_text())["servers"]["qasight"]
    assert block["type"] == "stdio" and block["command"] == "qasight"


def test_claude_desktop_gets_absolute_paths_in_its_own_file(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "desktop" / "claude_desktop_config.json"
    monkeypatch.setattr(mcp_install, "desktop_config_path", lambda: target)

    result = CliRunner().invoke(
        app, ["mcp", "install", "--dir", str(project), "--client", "claude-desktop"]
    )

    assert result.exit_code == 0, result.output
    block = json.loads(target.read_text())["mcpServers"]["qasight"]
    assert Path(block["command"]).is_absolute()
    assert block["args"] == ["mcp", "--config", str((project / "qasight.yaml").resolve())]


def test_desktop_config_path_per_os() -> None:
    env = {"HOME": "/h", "APPDATA": "C:/AppData", "XDG_CONFIG_HOME": ""}
    assert mcp_install.desktop_config_path(env, "darwin") == Path(
        "/h/Library/Application Support/Claude/claude_desktop_config.json"
    )
    assert mcp_install.desktop_config_path(env, "win32").parent == Path("C:/AppData/Claude")
    assert mcp_install.desktop_config_path(env, "linux") == Path(
        "/h/.config/Claude/claude_desktop_config.json"
    )


def test_a_broken_config_file_is_left_alone(project: Path) -> None:
    (project / ".mcp.json").write_text("[1, 2]")

    result = CliRunner().invoke(app, ["mcp", "install", "--dir", str(project)])

    assert result.exit_code == 2
    assert (project / ".mcp.json").read_text() == "[1, 2]"


def test_an_unknown_client_is_exit_2(project: Path) -> None:
    result = CliRunner().invoke(app, ["mcp", "install", "--dir", str(project), "--client", "emacs"])
    assert result.exit_code == 2
