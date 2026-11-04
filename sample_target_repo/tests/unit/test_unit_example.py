"""Unit-only tests for ``mock_api`` (no HTTP).

These tests exercise the in-process helpers of the sandbox API directly:
``Item``, ``_find_item``, ``_issue_token``, and ``_require_bearer``. They
must not touch the FastAPI app or open any sockets so they stay in the
fast ``unit`` lane of the qasight plans.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import jwt
import mock_api
import pytest
from fastapi import HTTPException
from mock_api import (
    JWT_ALG,
    JWT_SECRET,
    Item,
    _find_item,
    _issue_token,
    _require_bearer,
)


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:
    """Reset the module-level ``ITEMS`` list and ``NEXT_ID`` counter.

    The mock API stores state on module globals to keep the sandbox tiny.
    Unit tests that exercise ``_find_item`` mutate those globals, so we
    must scrub before and after every test to keep cases independent.
    """

    mock_api.ITEMS.clear()
    mock_api.NEXT_ID = 1
    yield
    mock_api.ITEMS.clear()
    mock_api.NEXT_ID = 1


# ---------------------------------------------------------------------------
# Item dataclass — construction, meta types, equality (10 + 10 + 5 = 25 cases)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "item_id,name,meta",
    [
        (1, "alpha", {}),
        (2, "beta", {"k": "v"}),
        (3, "gamma", {"n": 1}),
        (10, "x", {"a": "b", "c": "d"}),
        (42, "answer", {"nested": {"deep": True}}),
        (100, "centi", {"list": [1, 2, 3]}),
        (999, "near-max", {"flag": False}),
        (7, "lucky", {"score": 0.5}),
        (0, "zero", {"empty": ""}),
        (12345, "big", {"k1": "v1", "k2": "v2", "k3": "v3"}),
    ],
)
def test_item_construction_roundtrips_fields(item_id: int, name: str, meta: dict[str, Any]) -> None:
    item = Item(id=item_id, name=name, meta=meta)
    assert item.id == item_id
    assert item.name == name
    assert item.meta == meta


@pytest.mark.unit
@pytest.mark.parametrize(
    "meta",
    [
        {},
        {"str": "val"},
        {"int": 1},
        {"float": 1.5},
        {"bool": True},
        {"none": None},
        {"list": [1, 2, 3]},
        {"tuple": (1, 2)},
        {"nested": {"a": {"b": "c"}}},
        {"mixed": [1, "two", 3.0, None, True]},
    ],
)
def test_item_meta_accepts_arbitrary_dict_values(meta: dict[str, Any]) -> None:
    item = Item(id=1, name="x", meta=meta)
    assert item.meta == meta


@pytest.mark.unit
@pytest.mark.parametrize(
    "a,b,equal",
    [
        ((1, "x", {}), (1, "x", {}), True),
        ((1, "x", {"k": "v"}), (1, "x", {"k": "v"}), True),
        ((1, "x", {}), (2, "x", {}), False),
        ((1, "x", {}), (1, "y", {}), False),
        ((1, "x", {"a": 1}), (1, "x", {"a": 2}), False),
    ],
)
def test_item_equality_matches_dataclass_semantics(
    a: tuple[int, str, dict[str, Any]],
    b: tuple[int, str, dict[str, Any]],
    equal: bool,
) -> None:
    assert (Item(*a) == Item(*b)) is equal


# ---------------------------------------------------------------------------
# _find_item — empty list, miss, hit (1 + 10 + 10 = 21 cases)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_find_item_returns_none_when_list_empty() -> None:
    assert _find_item(999) is None


@pytest.mark.unit
@pytest.mark.parametrize("missing_id", [0, -1, 999, 2, 3, 4, 5, 100, 50, -50])
def test_find_item_returns_none_when_id_absent(missing_id: int) -> None:
    mock_api.ITEMS.append(Item(id=1, name="only", meta={}))
    assert _find_item(missing_id) is None


@pytest.mark.unit
@pytest.mark.parametrize("target_id", list(range(1, 11)))
def test_find_item_returns_first_matching_item(target_id: int) -> None:
    for i in range(1, 11):
        mock_api.ITEMS.append(Item(id=i, name=f"item-{i}", meta={"i": i}))
    found = _find_item(target_id)
    assert found is not None
    assert found.id == target_id
    assert found.name == f"item-{target_id}"


# ---------------------------------------------------------------------------
# _issue_token — shape, payload, expiry (5 + 10 + 5 = 20 cases)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("username", ["alice", "bob", "carol", "dave", "eve"])
def test_issue_token_returns_non_empty_string(username: str) -> None:
    token = _issue_token(username)
    assert isinstance(token, str)
    assert token


@pytest.mark.unit
@pytest.mark.parametrize(
    "username",
    [
        "alice",
        "bob",
        "user-123",
        "admin",
        "x",
        "with space",
        "u@example.com",
        "u/with/slash",
        "üñîçødé",
        "0",
    ],
)
def test_issue_token_payload_round_trips_username(username: str) -> None:
    token = _issue_token(username)
    decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    assert decoded["sub"] == username


@pytest.mark.unit
@pytest.mark.parametrize("username", ["a", "b", "c", "d", "e"])
def test_issue_token_expires_one_hour_after_iat(username: str) -> None:
    token = _issue_token(username)
    decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    assert decoded["exp"] - decoded["iat"] == 3600


# ---------------------------------------------------------------------------
# _require_bearer — rejects, accepts, details (5 + 5 + 5 + 10 + 5 + 4 = 34)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("auth", [None, "", " ", "  ", "\t"])
def test_require_bearer_rejects_missing_or_blank_header(auth: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_bearer(auth)
    assert exc.value.status_code == 401


@pytest.mark.unit
@pytest.mark.parametrize(
    "auth",
    [
        "Basic abc",
        "Token xyz",
        "JWT something",
        "bearertoken",
        "X-Bearer foo",
    ],
)
def test_require_bearer_rejects_wrong_scheme(auth: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_bearer(auth)
    assert exc.value.status_code == 401


@pytest.mark.unit
@pytest.mark.parametrize(
    "garbage",
    ["abc", "not.a.jwt", "x.y.z", "definitely-not-jwt", "..."],
)
def test_require_bearer_rejects_undecodable_token(garbage: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_bearer(f"Bearer {garbage}")
    assert exc.value.status_code == 401


@pytest.mark.unit
@pytest.mark.parametrize(
    "username",
    [
        "alice",
        "bob",
        "carol",
        "dave",
        "eve",
        "frank",
        "grace",
        "heidi",
        "ivan",
        "judy",
    ],
)
def test_require_bearer_returns_claims_for_valid_token(username: str) -> None:
    token = _issue_token(username)
    claims = _require_bearer(f"Bearer {token}")
    assert claims["sub"] == username
    assert "iat" in claims
    assert "exp" in claims


@pytest.mark.unit
@pytest.mark.parametrize(
    "prefix",
    ["Bearer", "bearer", "BEARER", "BeArEr", "bEaReR"],
)
def test_require_bearer_scheme_check_is_case_insensitive(prefix: str) -> None:
    token = _issue_token("alice")
    claims = _require_bearer(f"{prefix} {token}")
    assert claims["sub"] == "alice"


@pytest.mark.unit
@pytest.mark.parametrize(
    "auth,expected_detail",
    [
        (None, "missing bearer token"),
        ("", "missing bearer token"),
        ("Basic xxx", "missing bearer token"),
        ("Bearer not-a-token", "invalid token"),
    ],
)
def test_require_bearer_exception_carries_expected_detail(
    auth: str | None, expected_detail: str
) -> None:
    with pytest.raises(HTTPException) as exc:
        _require_bearer(auth)
    assert exc.value.detail == expected_detail
