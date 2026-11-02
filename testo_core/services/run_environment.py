"""The environment a run tested: browser, OS, runtime, target... as one canonical string.

A run's environment is stored in its metadata under ``environment`` as
``"browser=firefox, os=linux"``: keys lower-case and sorted, so two runs on the
same environment always compare equal. It comes from, first to last:

1. ``--env KEY=VALUE`` on ``testo run`` / ``testo ingest`` (or an upload's
   ``environment`` field, which the client fills from the same places);
2. the ``TESTO_ENV`` variable, ``"browser=firefox,os=linux"`` (handy in a CI matrix);
3. Allure's own ``environment.properties`` file in the run's results.

Flaky detection keeps each environment apart, so a test that always fails on one
browser is "fails on firefox", not "flaky". Nothing is filled in automatically:
an unset environment is one environment, as before.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

ENV_VAR = "TESTO_ENV"
METADATA_KEY = "environment"
MAX_KEYS = 8
_KEY = re.compile(r"[^a-z0-9_.-]+")
_KEY_LIMIT = 40
_VALUE_LIMIT = 60
_PROPERTIES_LIMIT = 64 * 1024


class InvalidEnvironmentPair(ValueError):
    """A ``KEY=VALUE`` pair that can't be read (no ``=``, or an empty key)."""


def _clean_key(key: str) -> str:
    return _KEY.sub("-", key.strip().lower()).strip("-")[:_KEY_LIMIT]


def _clean_value(value: str) -> str:
    # Commas and "=" separate pairs in the stored string.
    return " ".join(value.replace(",", " ").replace("=", " ").split())[:_VALUE_LIMIT]


def from_pairs(pairs: Iterable[str], *, strict: bool = False) -> dict[str, str]:
    """``["browser=firefox", "os=linux"]`` as a dict; bad pairs are skipped (or raise with ``strict``)."""
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        name, cleaned = _clean_key(key), _clean_value(value)
        if not sep or not name or not cleaned:
            if strict:
                raise InvalidEnvironmentPair(f"expected KEY=VALUE, got {pair!r}")
            continue
        out[name] = cleaned
    return dict(sorted(out.items())[:MAX_KEYS])


def parse(text: str | None) -> dict[str, str]:
    """``"browser=firefox, os=linux"`` (the stored form, or ``TESTO_ENV``) as a dict."""
    return from_pairs((text or "").split(","))


def canonical(env: Mapping[str, str]) -> str:
    """The stored form: ``"browser=firefox, os=linux"``, keys sorted; ``""`` when empty."""
    cleaned = from_pairs(f"{k}={v}" for k, v in env.items())
    return ", ".join(f"{k}={v}" for k, v in cleaned.items())


def from_environ(environ: Mapping[str, str]) -> str:
    return canonical(parse(environ.get(ENV_VAR)))


def from_allure(root: Path) -> str:
    """The first Allure ``environment.properties`` under ``root``, in the stored form."""
    if not root.is_dir():
        return ""
    for path in sorted(root.rglob("environment.properties")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:_PROPERTIES_LIMIT]
        except OSError:
            continue
        lines = (ln.strip() for ln in text.splitlines())
        pairs = [ln.replace(":", "=", 1) if "=" not in ln else ln for ln in lines]
        env = canonical(from_pairs(p for p in pairs if p and not p.startswith(("#", "!"))))
        if env:
            return env
    return ""


def resolve(explicit: str | None, environ: Mapping[str, str], results_root: Path | None) -> str:
    """The run's environment: ``explicit``, else ``TESTO_ENV``, else Allure's file; ``""`` if none."""
    if explicit and (env := canonical(parse(explicit))):
        return env
    if env := from_environ(environ):
        return env
    return from_allure(results_root) if results_root is not None else ""


def apply_cli_pairs(pairs: Iterable[str], environ: dict[str, str] | Mapping[str, str]) -> None:
    """Merge ``--env KEY=VALUE`` flags into ``TESTO_ENV`` (the flags win on the same key).

    The CLI sets the variable for its own process, so the run record, an upload to
    a team server and the stages' own processes all see the same environment.
    Raises :class:`InvalidEnvironmentPair` for a flag without ``=``.
    """
    given = from_pairs(list(pairs), strict=True)
    if not given:
        return
    merged = {**parse(environ.get(ENV_VAR)), **given}
    environ[ENV_VAR] = canonical(merged)  # type: ignore[index]
