"""The release a run tested ("2.3", "2026.11", "v2.3.0-rc1"), stored in its metadata.

A run's release is stored under ``release``. It comes from, first to last:

1. ``--release`` on ``qasight run`` / ``qasight ingest`` (or an upload's ``release``
   field, which the client fills from the same places);
2. the ``TESTO_RELEASE`` variable (handy in CI: ``TESTO_RELEASE: ${{ github.ref_name }}``).

Nothing is guessed from git: a run without one belongs to no release. The
release page (:mod:`~qasight.services.releases`) groups runs by this value.
Stdlib only, so the persistence layer can read it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping

ENV_VAR = "TESTO_RELEASE"
METADATA_KEY = "release"
MAX_LENGTH = 60
_ALLOWED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")


class InvalidRelease(ValueError):
    """A release name with characters it can't carry (it goes into URLs and file names)."""


def clean(value: str | None, *, strict: bool = False) -> str:
    """``value`` trimmed and checked; ``""`` when empty or unusable (or raise with ``strict``)."""
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_LENGTH or not _ALLOWED.match(text):
        if strict:
            raise InvalidRelease(
                f"{text[:MAX_LENGTH]!r}: use up to {MAX_LENGTH} letters, digits, '.', '-', '+' or '_'"
            )
        return ""
    return text


def resolve(explicit: object, environ: Mapping[str, str]) -> str:
    """The run's release: the explicit value (``--release`` or an upload), else ``TESTO_RELEASE``."""
    if isinstance(explicit, str) and (value := clean(explicit)):
        return value
    return clean(environ.get(ENV_VAR))


def apply_cli_option(value: str | None, environ: MutableMapping[str, str]) -> None:
    """Make ``--release`` the run's release by setting ``TESTO_RELEASE`` (raises on a bad name)."""
    if value is None:
        return
    environ[ENV_VAR] = clean(value, strict=True)
