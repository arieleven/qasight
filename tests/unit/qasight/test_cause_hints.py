from __future__ import annotations

import pytest

from qasight.services.cause_hints import suggest_cause


@pytest.mark.parametrize(
    ("message", "cause"),
    [
        ("ConnectionRefusedError: [Errno 111] Connection refused", "environment"),
        ("getaddrinfo ENOTFOUND db.internal", "environment"),
        ("OSError: [Errno 28] No space left on device", "environment"),
        ("requests.exceptions.HTTPError: 503 Service Unavailable", "environment"),
        ("ModuleNotFoundError: No module named 'app'", "test_bug"),
        ("fixture 'client' not found", "test_bug"),
        ("AssertionError: assert 2 == 3", None),
        ("", None),
        (None, None),
    ],
)
def test_suggest_cause(message: str | None, cause: str | None) -> None:
    assert suggest_cause(message) == cause


def test_a_marked_cause_wins_over_a_suggestion() -> None:
    from qasight.services.run_failures import FailingTest, _with_suggestions

    base = dict(
        key="k",
        name="n",
        full_name="",
        group="",
        stage="",
        status="failed",
        message="Connection refused",
        duration_ms=None,
        failing_runs=1,
        failing_since_run_id="r",
        flaky=False,
        new=True,
    )
    plain, marked = FailingTest(**base), FailingTest(**base, triage=object())  # type: ignore[arg-type]
    out = _with_suggestions([plain, marked])
    assert out[0].suggested_cause == "environment"
    assert out[1].suggested_cause is None
