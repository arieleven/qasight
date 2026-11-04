"""``workers`` set by the user vs the built-in default: only the first warns
when pytest-xdist is missing (the engineering health review's U1)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from qasight.config.loader import load_config

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]


def _stage(tmp_path: Path, body: str) -> object:
    cfg = tmp_path / "qasight.yaml"
    cfg.write_text(textwrap.dedent(body), encoding="utf-8")
    return load_config(cfg).cycles["app"].stages[0]


def test_stage_on_the_built_in_default_is_not_explicit(tmp_path: Path) -> None:
    stage = _stage(
        tmp_path,
        """\
        version: 1
        cycles:
          app:
            stages:
              - name: unit
                framework: pytest
        """,
    )
    assert stage.workers == 4
    assert stage.workers_explicit is False


def test_workers_on_the_stage_is_explicit(tmp_path: Path) -> None:
    stage = _stage(
        tmp_path,
        """\
        version: 1
        cycles:
          app:
            stages:
              - name: unit
                framework: pytest
                workers: 4
        """,
    )
    assert stage.workers_explicit is True


def test_workers_in_defaults_is_explicit(tmp_path: Path) -> None:
    stage = _stage(
        tmp_path,
        """\
        version: 1
        defaults:
          workers: 2
        cycles:
          app:
            stages:
              - name: unit
                framework: pytest
        """,
    )
    assert (stage.workers, stage.workers_explicit) == (2, True)
