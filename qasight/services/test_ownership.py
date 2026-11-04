"""Who owns a test, and a prefilled issue to hand it over.

The owner comes from the repository's CODEOWNERS file (``CODEOWNERS``,
``.github/CODEOWNERS`` or ``docs/CODEOWNERS``, GitHub's order), matched
against the test's source file as far as it can be told from the Allure
result: a path in ``fullName`` (``tests/test_a.py::test_x``,
``features/login.feature``) or a dotted module (``tests.api.test_orders``
becomes ``tests/api/test_orders.py``). Like GitHub, the last matching rule
wins. The issue link opens GitHub's new-issue form, filled in; nothing is
sent anywhere until the person submits it there.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

CODEOWNERS_LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TEST_MODULE = re.compile(r"test_\w+|\w+_test")
_TITLE_LIMIT = 200
_MESSAGE_LIMIT = 1500


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """A CODEOWNERS (gitignore-style) pattern as a regex over a repo-relative path."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    dir_only = pattern.endswith("/")
    body = pattern.strip("/")
    out = ""
    i = 0
    while i < len(body):
        if body.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif body.startswith("**", i):
            out += ".*"
            i += 2
        elif body[i] == "*":
            out += "[^/]*"
            i += 1
        elif body[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(body[i])
            i += 1
    prefix = "^" if anchored else "^(?:.*/)?"
    suffix = "/.*$" if dir_only else "(?:/.*)?$"
    return re.compile(prefix + out + suffix)


@lru_cache(maxsize=32)
def _rules(codeowners: Path, mtime: float) -> tuple[tuple[re.Pattern[str], tuple[str, ...]], ...]:
    del mtime  # part of the cache key, so an edited file is read again
    rules: list[tuple[re.Pattern[str], tuple[str, ...]]] = []
    for raw in codeowners.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        rules.append((_pattern_regex(pattern), tuple(owners)))
    return tuple(rules)


def find_codeowners(repo: Path | None) -> Path | None:
    if repo is None:
        return None
    for name in CODEOWNERS_LOCATIONS:
        candidate = repo / name
        if candidate.is_file():
            return candidate
    return None


def source_path(full_name: str, group: str = "") -> str | None:
    """The test's file relative to the repo root, as far as its names tell."""
    for name in (full_name, group):
        name = name.strip()
        if not name:
            continue
        # pytest node ids and Behave locations already carry the path.
        head = re.split(r"::|:(?=\d|\s)", name, maxsplit=1)[0]
        if "/" in head or head.endswith((".py", ".feature")):
            return head.lstrip("./")
        module = name.split("#", 1)[0]
        if re.fullmatch(r"[A-Za-z_][\w.]*", module) and "." in module:
            parts = module.split(".")
            # "tests.api.test_orders.TestX.test_create": the first test_*/*_test part is the file.
            cut = next((i for i, part in enumerate(parts) if _TEST_MODULE.fullmatch(part)), None)
            if cut is not None:
                parts = parts[: cut + 1]
            else:
                while len(parts) > 1 and parts[-1][:1].isupper():
                    parts.pop()
            return "/".join(parts) + ".py"
    return None


def owners_for(repo: Path | None, full_name: str, group: str = "") -> tuple[str, ...]:
    """CODEOWNERS owners of the test's file; empty when there is no file or no match."""
    codeowners = find_codeowners(repo)
    path = source_path(full_name, group)
    if codeowners is None or path is None:
        return ()
    owners: tuple[str, ...] = ()
    for regex, rule_owners in _rules(codeowners, codeowners.stat().st_mtime):
        if regex.match(path):
            owners = rule_owners
    return owners


def issue_url(
    repository: str | None,
    *,
    cycle: str,
    test_name: str,
    full_name: str = "",
    message: str = "",
    owners: tuple[str, ...] = (),
) -> str | None:
    """GitHub's new-issue form, prefilled for a flaky or failing test; ``None`` without a repo."""
    if not repository or not _GITHUB_REPO.match(repository):
        return None
    lines = [
        f"`{full_name or test_name}` in cycle `{cycle}` is flaky or failing.",
        "",
    ]
    if message:
        lines += ["Last failure:", "", "```", message[:_MESSAGE_LIMIT], "```", ""]
    if owners:
        lines += [f"Owners (CODEOWNERS): {' '.join(owners)}", ""]
    lines.append("_Opened from QASight._")
    query = urlencode(
        {"title": f"Flaky test: {test_name}"[:_TITLE_LIMIT], "body": "\n".join(lines)}
    )
    return f"https://github.com/{repository}/issues/new?{query}"
