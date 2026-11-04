from __future__ import annotations

from pathlib import Path

import yaml


def test_gitlab_template_invokes_testo_run_ci() -> None:
    template = Path("ci/gitlab/testo.gitlab-ci.yml").read_text(encoding="utf-8")
    assert "TESTO_ARGS=(run --ci)" in template
    assert '--config "$TESTO_CONFIG_PATH"' in template
    assert '--cycle "$TESTO_CYCLE"' in template
    assert "--no-persist" in template
    assert 'qasight "${TESTO_ARGS[@]}"' in template


def test_gitlab_template_defines_summary_artifacts() -> None:
    payload = yaml.safe_load(Path("ci/gitlab/testo.gitlab-ci.yml").read_text(encoding="utf-8"))
    job = payload["testo_run"]
    artifacts = job["artifacts"]
    assert "testo-output.ndjson" in artifacts["paths"]
    assert "testo-summary.json" in artifacts["paths"]
