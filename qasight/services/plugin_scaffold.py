"""``qasight plugins new NAME``: a plugin package to start from.

The files are templates under ``qasight/templates/plugin/`` (Python files end
in ``.tmpl`` so linters and test collectors skip them), filled with the
plugin's name: ``qasight plugins new my-runner`` writes ``qasight-my-runner-plugin/``
with package ``qasight_my_runner_plugin``, the framework entry point
``my-runner`` (class ``MyRunnerAdapter``) and the reporter ``my-runner-summary``.
It is the repository's ``examples/plugin`` under another name, and works as
written: ``pip install -e .`` then ``qasight run --cycle unit``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "templates" / "plugin"
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")


class ScaffoldError(ValueError):
    """A bad plugin name, or a folder that exists."""


@dataclass(frozen=True)
class PluginNames:
    name: str
    dist: str
    module: str
    cls: str


def names_for(name: str) -> PluginNames:
    name = name.strip().lower()
    if not _NAME.match(name) or name.endswith("-") or "--" in name:
        raise ScaffoldError(
            "plugin names are lowercase letters, digits and single hyphens, starting with a "
            f"letter (it becomes `equipment: <name>`), not {name!r}"
        )
    parts = name.split("-")
    return PluginNames(
        name=name,
        dist=f"qasight-{name}-plugin",
        module=f"qasight_{'_'.join(parts)}_plugin",
        cls="".join(p.capitalize() for p in parts),
    )


def _fill(text: str, names: PluginNames) -> str:
    return (
        text.replace("__MODULE__", names.module)
        .replace("__DIST__", names.dist)
        .replace("__CLASS__", names.cls)
        .replace("__NAME__", names.name)
    )


def write_plugin(name: str, parent: Path) -> tuple[Path, PluginNames]:
    """Write the plugin package for *name* into ``parent/<dist>``; returns that folder."""
    names = names_for(name)
    root = parent / names.dist
    if root.exists():
        raise ScaffoldError(f"{root} already exists")
    for source in sorted(p for p in TEMPLATES.rglob("*") if p.is_file()):
        rel = source.relative_to(TEMPLATES).as_posix()
        if "__pycache__" in rel:
            continue
        rel = _fill(rel, names).removesuffix(".tmpl")
        if rel == "gitignore":
            rel = ".gitignore"
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_fill(source.read_text(encoding="utf-8"), names), encoding="utf-8")
    return root, names
