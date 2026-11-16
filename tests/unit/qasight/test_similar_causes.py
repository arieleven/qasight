"""Causes suggested from the causes marked on similar failures (services/similar_causes.py)."""

from __future__ import annotations

from qasight.services.similar_causes import CauseModel, Example, evaluate

_EXAMPLES = [
    Example(
        "shop",
        "environment",
        "ConnectionRefusedError: [Errno 111] connect to postgres:5432 refused",
    ),
    Example(
        "shop", "environment", "psycopg.OperationalError: could not connect to server at db:5432"
    ),
    Example("shop", "product_bug", "AssertionError: cart total 10.01 != 10.00 after discount"),
    Example(
        "shop", "product_bug", "AssertionError: order total 99.99 != 100.00 after coupon discount"
    ),
    Example("shop", "test_bug", "fixture 'logged_in_admin' not found"),
    Example(
        "billing", "flaky", "TimeoutError: page.click: Timeout 30000ms exceeded waiting for #pay"
    ),
]


def test_a_reworded_failure_gets_the_cause_of_the_similar_ones() -> None:
    model = CauseModel(_EXAMPLES)
    found = model.suggest("shop", "AssertionError: invoice total 5.01 != 5.00 after discount")
    assert found is not None
    assert found.cause == "product_bug"
    assert 0 < found.confidence <= 1
    assert "discount" in found.example_message


def test_unlike_anything_marked_gets_no_suggestion() -> None:
    model = CauseModel(_EXAMPLES)
    assert model.suggest("shop", "KeyError: 'sku'") is None
    assert model.suggest("shop", "") is None
    assert CauseModel([]).suggest("shop", "anything") is None


def test_other_cycles_count_less_but_still_count() -> None:
    model = CauseModel(_EXAMPLES)
    found = model.suggest(
        "shop", "TimeoutError: page.click: Timeout 30000ms exceeded waiting for #checkout"
    )
    assert found is not None and found.cause == "flaky" and found.example_cycle == "billing"


def test_evaluate_replays_each_cause_against_the_others() -> None:
    result = evaluate(_EXAMPLES)
    assert result.examples == len(_EXAMPLES)
    assert result.suggested >= 2
    assert result.precision == 1.0
    assert result.coverage is not None and 0 < result.coverage < 1
    assert evaluate([]).precision is None
