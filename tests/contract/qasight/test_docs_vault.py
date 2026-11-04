"""The docs/ vault obeys its own schema (docs/CLAUDE.md).

Runs the same lint CI runs, so a broken link, an orphan note or a note
without frontmatter fails locally before it fails in the `docs_vault` job.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "docs_vault.py"


def _load():
    spec = importlib.util.spec_from_file_location("docs_vault", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


docs_vault = _load()


def _write(root: Path, rel: str, body: str, *, status: str = "current") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: guide\nstatus: {status}\ncreated: 2026-01-01\nupdated: 2026-01-01\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.mark.contract
def test_vault_passes_lint() -> None:
    assert docs_vault.lint() == []


@pytest.mark.contract
def test_lint_reports_broken_links_orphans_and_wikilinks(tmp_path: Path) -> None:
    _write(tmp_path, "Index.md", "[a](A%20Note.md) [gone](missing.md) `[[code]]`")
    _write(tmp_path, "A Note.md", "see [[Index]]")
    _write(tmp_path, "Orphan.md", "nobody links here")
    (tmp_path / "Bare.md").write_text("# no frontmatter\n", encoding="utf-8")

    errors = "\n".join(docs_vault.lint(tmp_path))

    assert "Index.md: broken link to `missing.md`" in errors
    assert "A Note.md: uses [[wikilinks]]" in errors
    assert "Index.md: uses [[wikilinks]]" not in errors
    assert "Orphan.md: orphan" in errors
    assert "Bare.md: missing frontmatter" in errors


@pytest.mark.contract
def test_archive_status_must_match_folder(tmp_path: Path) -> None:
    _write(tmp_path, "Index.md", "[old](Archive/Old.md)")
    _write(tmp_path, "Archive/Old.md", "frozen")

    assert any("Archive/Old.md" in e and "archived" in e for e in docs_vault.lint(tmp_path))


@pytest.mark.contract
def test_wiki_page_names_are_flat_and_unique() -> None:
    assert docs_vault.wiki_page_name("Index.md") == "Home"
    assert docs_vault.wiki_page_name("Specs & ADRs/README.md") == "Specs-and-ADRs"
    assert (
        docs_vault.wiki_page_name("Architecture/Deep Dive - Execution Logic.md")
        == "Deep-Dive---Execution-Logic"
    )
    names = [docs_vault.wiki_page_name(n.rel) for n in docs_vault.load_notes()]
    assert len(names) == len(set(names))
