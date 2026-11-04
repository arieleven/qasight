"""Shared Rich :class:`Console` factory and CI detection.

A single console instance is preferable so all renderers respect the same
theme and width settings.  Commands that take a ``--ci`` flag should call
:func:`make_console` with ``plain=True`` to force a no-ANSI renderer.
"""

from __future__ import annotations

import os
import sys

from rich.console import Console
from rich.theme import Theme

_THEME = Theme(
    {
        "title": "bold cyan",
        "ok": "bold green",
        "fail": "bold red",
        "warn": "bold yellow",
        "muted": "dim",
        "framework": "magenta",
        "behavex": "bold cyan",
    }
)

_CI_ENV_KEYS: tuple[str, ...] = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "JENKINS_URL",
    "BUILDKITE",
    "CIRCLECI",
    "TF_BUILD",
)


def is_ci_environment(env: os._Environ[str] | dict[str, str] | None = None) -> bool:
    """Return ``True`` when one of the well-known CI markers is set."""
    env = env if env is not None else os.environ
    return any(env.get(key) for key in _CI_ENV_KEYS)


def make_console(*, plain: bool = False) -> Console:
    """Create a Rich :class:`Console` suitable for either humans or CI logs.

    The semantic theme is *always* applied (so styles like ``ok`` / ``fail``
    resolve), but in plain mode we suppress ANSI colour codes and disable
    Rich's syntax highlighting.
    """
    return Console(
        theme=_THEME,
        force_terminal=False if plain else None,
        no_color=plain,
        soft_wrap=True,
        stderr=False,
        highlight=not plain,
    )


def default_console() -> Console:
    """Return the console that humans should see when no flags override it."""
    plain = is_ci_environment() or not sys.stdout.isatty()
    return make_console(plain=plain)
