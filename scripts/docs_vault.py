#!/usr/bin/env python3
"""Lint the docs/ vault and export it to the GitHub wiki layout.

The rules this enforces are written down in docs/CLAUDE.md. Stdlib only, so it
runs in CI and in a pre-commit hook without installing the project.

    python scripts/docs_vault.py lint
    python scripts/docs_vault.py lint --base origin/main
    python scripts/docs_vault.py export-wiki OUT_DIR --repo owner/name --ref main
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404 - runs test tools; every call passes an argv list without a shell
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"
INDEX = "Index.md"
ARCHIVE_DIR = "Archive"

NOTE_TYPES = {
    "index",
    "schema",
    "log",
    "architecture",
    "reference",
    "guide",
    "spec",
    "roadmap",
    "tracker",
    "archive",
}
NOTE_STATUSES = {"current", "archived"}
REQUIRED_KEYS = ("type", "status", "created", "updated")

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FENCE_RE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.DOTALL | re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")
_WIKILINK_RE = re.compile(r"\[\[[^\]\n]+\]\]")
_EXTERNAL_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


@dataclass(frozen=True)
class Note:
    path: Path  # absolute
    rel: str  # posix path relative to docs/
    text: str

    @property
    def frontmatter(self) -> dict[str, str] | None:
        match = _FRONTMATTER_RE.match(self.text)
        if match is None:
            return None
        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep and not key.startswith((" ", "#")):
                fields[key.strip()] = value.strip()
        return fields

    @property
    def body(self) -> str:
        return _FRONTMATTER_RE.sub("", self.text, count=1)


def load_notes(docs_root: Path = DOCS_ROOT) -> list[Note]:
    notes = []
    for path in sorted(docs_root.rglob("*.md")):
        rel = path.relative_to(docs_root)
        if rel.parts[0].startswith("."):
            continue
        notes.append(Note(path, rel.as_posix(), path.read_text(encoding="utf-8")))
    return notes


def _mask_code(text: str) -> str:
    """Blank out fenced and inline code so example links are not treated as links.

    Length-preserving, so match offsets are valid in the original text.
    """
    text = _FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), text)


def iter_links(note: Note) -> Iterator[tuple[str, str]]:
    """Yield (target path, anchor) for every relative link in the note."""
    for match in _LINK_RE.finditer(_mask_code(note.body)):
        target = match.group(3)
        if _EXTERNAL_RE.match(target) or target.startswith("#"):
            continue
        path, _, anchor = target.partition("#")
        yield unquote(path), anchor


def _resolve(note: Note, target: str) -> Path:
    return (note.path.parent / target).resolve()


def lint(docs_root: Path = DOCS_ROOT) -> list[str]:
    notes = load_notes(docs_root)
    by_path = {note.path.resolve(): note for note in notes}
    errors: list[str] = []
    outgoing: dict[str, set[str]] = {}

    for note in notes:
        fields = note.frontmatter
        archived = note.rel.startswith(f"{ARCHIVE_DIR}/")
        if fields is None:
            errors.append(f"{note.rel}: missing frontmatter (see docs/CLAUDE.md)")
        else:
            for key in REQUIRED_KEYS:
                if not fields.get(key):
                    errors.append(f"{note.rel}: frontmatter is missing `{key}`")
            if fields.get("type") and fields["type"] not in NOTE_TYPES:
                errors.append(f"{note.rel}: unknown type `{fields['type']}`")
            if fields.get("status") and fields["status"] not in NOTE_STATUSES:
                errors.append(f"{note.rel}: unknown status `{fields['status']}`")
            for key in ("created", "updated"):
                if fields.get(key) and not _DATE_RE.match(fields[key]):
                    errors.append(f"{note.rel}: `{key}` must be YYYY-MM-DD")
            if archived != (fields.get("status") == "archived"):
                errors.append(
                    f"{note.rel}: notes under {ARCHIVE_DIR}/ (and only those) "
                    "have status `archived`"
                )

        if _WIKILINK_RE.search(_mask_code(note.body)):
            errors.append(
                f"{note.rel}: uses [[wikilinks]]; use relative markdown links so "
                "the note also works on GitHub"
            )

        links: set[str] = set()
        for target, _anchor in iter_links(note):
            if not target:
                continue
            resolved = _resolve(note, target)
            if not resolved.exists():
                errors.append(f"{note.rel}: broken link to `{target}`")
            elif resolved in by_path:
                links.add(by_path[resolved].rel)
        outgoing[note.rel] = links

    if INDEX not in outgoing:
        errors.append(f"{INDEX}: the vault entry point is missing")
        return errors

    reachable = {INDEX}
    frontier = [INDEX]
    while frontier:
        for rel in outgoing[frontier.pop()]:
            if rel not in reachable:
                reachable.add(rel)
                frontier.append(rel)
    for note in notes:
        if note.rel not in reachable:
            errors.append(
                f"{note.rel}: orphan, not reachable from {INDEX}; link it from "
                "the index or the hub note of its folder"
            )
    return errors


def lint_updated_dates(base: str) -> list[str]:
    """Fail when a note changed against `base` but its `updated` date did not."""
    diff = subprocess.run(  # nosec B603 B607 - argv list, no shell; the tool is found on PATH
        ["git", "diff", "--name-only", "--diff-filter=AM", f"{base}...HEAD", "--", "docs"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    errors = []
    today = date.today().isoformat()
    for name in diff:
        path = REPO_ROOT / name
        if path.suffix != ".md" or "/." in name or not path.exists():
            continue
        note = Note(path, path.relative_to(DOCS_ROOT).as_posix(), path.read_text("utf-8"))
        old = subprocess.run(  # nosec B603 B607 - argv list, no shell; the tool is found on PATH
            ["git", "show", f"{base}:{name}"], cwd=REPO_ROOT, capture_output=True, text=True
        )
        if old.returncode != 0:
            continue  # new note
        old_note = Note(path, note.rel, old.stdout)
        if old_note.body == note.body:
            continue
        old_updated = (old_note.frontmatter or {}).get("updated")
        # A note already dated today needs no bump for a second edit the same day.
        if old_updated and old_updated == (note.frontmatter or {}).get("updated") != today:
            errors.append(f"{note.rel}: body changed but `updated` is still {old_updated}")
    return errors


# --- wiki export ---------------------------------------------------------


def wiki_page_name(rel: str) -> str:
    """Map a docs-relative path to its flat GitHub wiki page name."""
    path = Path(rel)
    if rel == INDEX:
        return "Home"
    stem = path.parent.name if path.name == "README.md" else path.stem
    stem = stem.replace("&", "and")
    return re.sub(r"\s+", "-", stem.strip())


def export_wiki(out_dir: Path, repo: str, ref: str) -> list[Path]:
    notes = load_notes()
    by_path = {note.path.resolve(): note for note in notes}
    names = {note.rel: wiki_page_name(note.rel) for note in notes}
    clashes = {n for n in names.values() if list(names.values()).count(n) > 1}
    if clashes:
        raise SystemExit(f"wiki page names collide: {sorted(clashes)}")

    blob = f"https://github.com/{repo}/blob/{ref}"
    raw = f"https://raw.githubusercontent.com/{repo}/{ref}"

    def rewrite(note: Note) -> str:
        masked = _mask_code(note.body)
        out: list[str] = []
        last = 0
        for match in _LINK_RE.finditer(masked):
            bang, label, target = match.groups()
            # Take the label from the original text; masking only blanks code.
            label = note.body[match.start(2) : match.end(2)]
            if _EXTERNAL_RE.match(target) or target.startswith("#"):
                continue
            path, _, anchor = target.partition("#")
            resolved = _resolve(note, unquote(path))
            suffix = f"#{anchor}" if anchor else ""
            if resolved in by_path:
                new = names[by_path[resolved].rel] + suffix
            else:
                try:
                    repo_rel = resolved.relative_to(REPO_ROOT).as_posix()
                except ValueError:
                    continue
                new = f"{raw if bang else blob}/{quote(repo_rel)}{suffix}"
            out.append(note.body[last : match.start()])
            out.append(f"{bang}[{label}]({new})")
            last = match.end()
        out.append(note.body[last:])
        return "".join(out)

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for note in notes:
        source = f"{blob}/docs/{quote(note.rel)}"
        page = (
            rewrite(note).rstrip() + f"\n\n---\n\n_Generated from [`docs/{note.rel}`]({source}). "
            "Edit it there; changes made in the wiki are overwritten._\n"
        )
        target = out_dir / f"{names[note.rel]}.md"
        target.write_text(page, encoding="utf-8")
        written.append(target)

    sidebar = ["**[Home](Home)**", ""]
    groups: dict[str, list[Note]] = {}
    for note in notes:
        if note.rel != INDEX:
            groups.setdefault(Path(note.rel).parent.as_posix(), []).append(note)
    for folder in sorted(groups, key=lambda f: (f == ARCHIVE_DIR, f == ".", f)):
        if folder == ARCHIVE_DIR:
            sidebar += [f"**[{ARCHIVE_DIR}]({ARCHIVE_DIR})**", ""]
            continue
        sidebar.append(f"**{'Vault' if folder == '.' else folder}**")
        for note in groups[folder]:
            path = Path(note.rel)
            label = path.parent.name if path.name == "README.md" else path.stem
            sidebar.append(f"- [{label}]({names[note.rel]})")
        sidebar.append("")
    target = out_dir / "_Sidebar.md"
    target.write_text("\n".join(sidebar), encoding="utf-8")
    written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    lint_parser = sub.add_parser("lint", help="check the vault against docs/CLAUDE.md")
    lint_parser.add_argument(
        "--base", help="also require `updated` to change on notes edited since this git ref"
    )
    wiki_parser = sub.add_parser("export-wiki", help="write the vault as GitHub wiki pages")
    wiki_parser.add_argument("out_dir", type=Path)
    wiki_parser.add_argument("--repo", required=True, help="owner/name")
    wiki_parser.add_argument("--ref", default="main")
    args = parser.parse_args(argv)

    if args.command == "lint":
        errors = lint()
        if args.base:
            errors += lint_updated_dates(args.base)
        for error in errors:
            print(error, file=sys.stderr)
        print(f"docs vault: {len(errors)} problem(s)" if errors else "docs vault: ok")
        return 1 if errors else 0

    written = export_wiki(args.out_dir, args.repo, args.ref)
    print(f"wrote {len(written)} wiki pages to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
