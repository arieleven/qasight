"""The ``resources:`` config section (qasight/resources/pools.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from qasight.config.errors import ConfigValidationError
from qasight.config.loader import load_config
from qasight.resources.pools import parse_pool, parse_resources


def test_items_with_labels_and_data_and_count_pools() -> None:
    pools = parse_resources(
        {
            "vms": {
                "items": [
                    "vm-1",
                    {
                        "id": "vm-2",
                        "labels": ["win11"],
                        "data": {"address": "10.0.0.2", "port": 22},
                    },
                ],
                "lease_ttl": 30,
                "max_per_user": 1,
                "on_take": "  revert $TESTO_RESOURCE_ID  ",
            },
            "seats": {"count": 3},
        }
    )
    vms, seats = pools["vms"], pools["seats"]
    assert [i.id for i in vms.items] == ["vm-1", "vm-2"]
    assert vms.items[1].labels == ("win11",)
    assert vms.items[1].data == (("address", "10.0.0.2"), ("port", "22"))
    assert (vms.lease_ttl_s, vms.max_per_user, vms.on_take) == (
        30.0,
        1,
        "revert $TESTO_RESOURCE_ID",
    )
    assert [i.id for i in vms.matching(["win11"])] == ["vm-2"]
    assert [i.id for i in seats.items] == ["seats-1", "seats-2", "seats-3"]
    assert seats.lease_ttl_s == 120.0


def test_a_pool_survives_the_trip_through_json() -> None:
    pool = parse_pool(
        "vms", {"items": [{"id": "vm-1", "labels": ["a"], "data": {"x": "1"}}], "on_take": "t"}
    )
    assert parse_pool("vms", pool.to_json()) == pool
    # A server never gets the commands.
    assert "on_take" not in pool.to_json(hooks=False)
    assert parse_pool("vms", pool.to_json(hooks=False)).on_take is None
    checked = parse_pool("vms", {"count": 2, "on_check": "ping", "reuse_delay": 0})
    assert parse_pool("vms", checked.to_json()) == checked
    assert "on_check" not in checked.to_json(hooks=False)
    assert parse_pool("vms", checked.to_json(hooks=False)).reuse_delay == 0


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "exactly one of 'items' or 'count'"),
        ({"items": ["a"], "count": 2}, "exactly one of 'items' or 'count'"),
        ({"count": 0}, "count must be a whole number of at least 1"),
        ({"count": True}, "count must be a whole number"),
        ({"items": []}, "items must be a non-empty list"),
        ({"items": ["a", "a"]}, "listed twice"),
        ({"items": ["bad key"]}, "must be 1-128 letters"),
        ({"items": [{"id": "a", "colour": "red"}]}, "unknown key(s) ['colour']"),
        ({"items": [{"id": "a", "data": {"x": [1]}}]}, "must be a plain value"),
        ({"items": ["a"], "lease_ttl": 0}, "lease_ttl must be a number of seconds above 0"),
        ({"items": ["a"], "on_take": ""}, "on_take must be a non-empty command"),
        ({"items": ["a"], "on_check": 3}, "on_check must be a non-empty command"),
        ({"items": ["a"], "reuse_delay": -1}, "reuse_delay must be a number of seconds 0 or more"),
        ({"items": ["a"], "ttl": 5}, "unknown key(s) ['ttl']"),
    ],
)
def test_mistakes_say_where(raw: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigValidationError, match=None) as info:
        parse_pool("vms", raw)
    assert message in str(info.value)
    assert "resources.vms" in str(info.value)


def test_the_loader_reads_pools_next_to_cycles_or_alone(tmp_path: Path) -> None:
    with_cycles = tmp_path / "a" / "qasight.yaml"
    with_cycles.parent.mkdir()
    with_cycles.write_text(
        "cycles:\n  unit:\n    stages:\n      - {name: u, framework: pytest}\n"
        "resources:\n  vms: {items: [vm-1]}\n",
        encoding="utf-8",
    )
    assert list(load_config(with_cycles).resources) == ["vms"]

    alone = tmp_path / "b" / "qasight.yaml"
    alone.parent.mkdir()
    alone.write_text("resources:\n  seats: {count: 2}\n", encoding="utf-8")
    config = load_config(alone)
    assert config.cycles == {}
    assert [i.id for i in config.resources["seats"].items] == ["seats-1", "seats-2"]
