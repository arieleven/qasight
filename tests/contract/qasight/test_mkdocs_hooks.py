"""The MkDocs hooks (scripts/mkdocs_hooks.py) map vault notes onto the docs site.

URLs must stay stable once published, and links that leave docs/ must point
at GitHub instead of becoming broken site links.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "mkdocs_hooks.py"


def _load():
    spec = importlib.util.spec_from_file_location("mkdocs_hooks", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hooks = _load()


@pytest.mark.contract
@pytest.mark.parametrize(
    ("src", "dest"),
    [
        ("Index.md", "index.html"),
        ("Getting Started.md", "getting-started/index.html"),
        ("CLI Commands/Command Reference.md", "cli-commands/command-reference/index.html"),
        ("Specs & ADRs/README.md", "specs-and-adrs/index.html"),
        (
            "Architecture/Deep Dive - Execution Logic.md",
            "architecture/deep-dive-execution-logic/index.html",
        ),
        ("CLAUDE.md", "vault-schema/index.html"),
    ],
)
def test_page_urls(src: str, dest: str) -> None:
    assert hooks.page_dest(src) == dest


@pytest.mark.contract
def test_links_outside_docs_point_at_github() -> None:
    markdown = (
        "[arch](../../ARCHITECTURE.md#flow) [note](../Index.md) "
        "![pic](../../docs/assets/logo.png) [web](https://example.com) `[code](../../x.md)`"
    )

    out = hooks.rewrite_outside_links(markdown, "CLI Commands/Command Reference.md")

    assert "[arch](https://github.com/arieleven/qasight/blob/main/ARCHITECTURE.md#flow)" in out
    assert "[note](../Index.md)" in out
    assert "![pic](../../docs/assets/logo.png)" in out
    assert "[web](https://example.com)" in out
    assert "`[code](../../x.md)`" in out


_DOCS_SITE = "https://arieleven.github.io/qasight/docs/"
# Code that prints or writes docs-site links for users to follow.
_LINKING_MODULES = [
    "qasight/services/project_setup.py",
    "qasight/services/ci_templates.py",
    "qasight/cli/commands/init_cmd.py",
    "qasight/cli/commands/try_cmd.py",
]


@pytest.mark.contract
def test_docs_site_links_in_generated_files_resolve() -> None:
    """Every docs-site URL that `qasight init` and friends write is a page the site builds."""
    import re

    hooks = _load()
    root = _SCRIPT.parents[1]
    docs = root / "docs"
    pages = {
        "",
        *(
            hooks.page_dest(p.relative_to(docs).as_posix()).removesuffix("index.html")
            for p in docs.rglob("*.md")
        ),
    }
    seen = 0
    for rel in _LINKING_MODULES:
        source = (root / rel).read_text(encoding="utf-8")
        for match in re.finditer(re.escape(_DOCS_SITE) + r"([^\s\"'`)#]*)", source):
            seen += 1
            assert match.group(1) in pages, f"{rel}: {match.group(0)} is not a docs site page"
    assert seen, "no docs-site links found; update _LINKING_MODULES"
