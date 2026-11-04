"""``qasight mcp install``: add ``qasight mcp`` to a coding agent's MCP config.

Each client keeps the same ``{"command", "args"}`` block in a different file
(and VS Code under ``servers`` instead of ``mcpServers``). Project files
(``.mcp.json``, ``.cursor/mcp.json``, ``.vscode/mcp.json``) are shared through
git, so they name ``qasight`` and the config relative to the project; Claude
Desktop's file is per machine and gets absolute paths. Other servers in the
file are kept; an older ``qasight`` entry is replaced.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SERVER_NAME = "qasight"
CLIENTS: tuple[str, ...] = ("claude-code", "cursor", "vscode", "claude-desktop")


@dataclass(frozen=True)
class InstallResult:
    path: Path
    replaced: bool


def desktop_config_path(env: dict[str, str] | None = None, platform: str | None = None) -> Path:
    """Where Claude Desktop keeps ``claude_desktop_config.json`` on this OS."""
    env = dict(os.environ if env is None else env)
    platform = platform or sys.platform
    home = Path(env.get("HOME") or Path.home())
    if platform == "darwin":
        base = home / "Library" / "Application Support"
    elif platform.startswith("win"):
        base = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "Claude" / "claude_desktop_config.json"


def config_path(client: str, project: Path) -> Path:
    if client == "claude-code":
        return project / ".mcp.json"
    if client == "cursor":
        return project / ".cursor" / "mcp.json"
    if client == "vscode":
        return project / ".vscode" / "mcp.json"
    if client == "claude-desktop":
        return desktop_config_path()
    raise ValueError(f"unknown client {client!r}; pick one of {', '.join(CLIENTS)}")


def server_block(
    client: str, project: Path, *, config: Path | None, allow_writes: bool
) -> dict[str, Any]:
    """The ``qasight mcp`` entry for *client*: relative in a project file, absolute per machine."""
    per_machine = client == "claude-desktop"
    args = ["mcp"]
    if config is not None:
        args += [
            "--config",
            str(config.resolve() if per_machine else config_relative(config, project)),
        ]
    if allow_writes:
        args.append("--allow-writes")
    command = "qasight"
    if per_machine:
        # A desktop app doesn't get the shell's PATH (or the virtualenv's).
        command = shutil.which("qasight") or str(Path(sys.executable).with_name("qasight"))
    block: dict[str, Any] = {"command": command, "args": args}
    if client == "vscode":
        block = {"type": "stdio", **block}
    return block


def config_relative(config: Path, project: Path) -> str:
    try:
        return config.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return str(config.resolve())


def install(
    client: str, project: Path, *, config: Path | None, allow_writes: bool = False
) -> InstallResult:
    """Add or replace the ``qasight`` server in *client*'s MCP config file."""
    path = config_path(client, project)
    key = "servers" if client == "vscode" else "mcpServers"
    data: dict[str, Any] = {}
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            loaded = json.loads(text)
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} is not a JSON object")
            data = loaded
    servers = data.get(key)
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise ValueError(f'"{key}" in {path} is not a JSON object')
    replaced = SERVER_NAME in servers
    servers[SERVER_NAME] = server_block(client, project, config=config, allow_writes=allow_writes)
    data[key] = servers
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return InstallResult(path=path, replaced=replaced)
