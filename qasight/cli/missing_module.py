"""One line instead of a traceback when a qasight dependency isn't installed.

``qasight ui`` without the ``api`` extra, ``qasight otel`` without ``otel``, or an
environment an upgrade half-broke all end in ``ModuleNotFoundError``. The
package's own metadata (``importlib.metadata.requires``) says which extra
brings the module, so the hint can't drift from ``pyproject.toml``. A module
that isn't one of ours is left to the normal traceback.
"""

from __future__ import annotations

import re

# Import names that differ from the distribution's name.
_ALIASES = {
    "yaml": "pyyaml",
    "dotenv": "python_dotenv",
    "multipart": "python_multipart",
    "jwt": "pyjwt",
    "psycopg_binary": "psycopg",
}
# Extras that hold a module only because they bundle everything; never the one to suggest.
_BUNDLES = frozenset({"dev", "all", "docs"})


def _norm(name: str) -> str:
    return re.sub(r"[-.]+", "_", name).lower()


def missing_module_hint(module: str | None) -> str | None:
    """``pip install`` that brings *module* back, or ``None`` if qasight doesn't need it."""
    if not module:
        return None
    from importlib.metadata import PackageNotFoundError, requires

    try:
        reqs = requires("qasight") or []
    except PackageNotFoundError:
        return None
    top = _norm(module.split(".")[0])
    wanted = {_norm(module), top, _ALIASES.get(top, top)}
    extras: list[str] = []
    dist_name = ""
    for req in reqs:
        dist = re.split(r"[\s\[<>=!~;]", req, maxsplit=1)[0]
        norm = _norm(dist)
        if norm not in wanted and not any(norm.startswith(w + "_") for w in wanted if w == top):
            continue
        dist_name = dist_name or dist
        match = re.search(r"extra\s*==\s*['\"]([^'\"]+)['\"]", req)
        if match is None:
            return (
                f"qasight's dependency {dist} is missing (a broken or half-upgraded "
                "environment): pip install --force-reinstall qasight"
            )
        extras.append(match.group(1))
    useful = [e for e in extras if e not in _BUNDLES]
    if not useful:
        return None
    return f"This needs the {useful[0]} extra ({dist_name}): pip install 'qasight[{useful[0]}]'"
