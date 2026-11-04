from __future__ import annotations

from pathlib import Path

from tests import conftest as test_conftest


def test_path_kind_recognizes_both_contract_directories() -> None:
    assert test_conftest._path_kind(Path("tests/contract/api/test_runs_contract.py")) == "contract"
    assert test_conftest._path_kind(Path("tests/contracts/test_contract_models.py")) == "contract"


def test_path_kind_sets_expected_families() -> None:
    assert test_conftest._path_kind(Path("tests/unit/qasight/test_cli_run.py")) == "unit"
    assert (
        test_conftest._path_kind(Path("tests/integration/api/test_run_lifecycle.py"))
        == "integration"
    )
    assert test_conftest._path_kind(Path("tests/e2e/sandbox_api/test_user_journeys.py")) == "e2e"


def test_run_id_sanitization_is_deterministic_and_safe() -> None:
    raw = " run id/with spaces + symbols "
    sanitized = test_conftest._sanitize_run_id(raw)
    assert sanitized == "run-id-with-spaces-symbols"
    assert len(sanitized) <= 48
