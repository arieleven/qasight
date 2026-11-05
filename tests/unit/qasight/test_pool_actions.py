"""``qasight pool add/remove/sweep/log``: your own create/destroy, only when a person asks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from qasight.cli.app import app
from qasight.resources.pools import parse_pool
from qasight.resources.provider import (
    MAX_KEYS_PER_COMMAND,
    Listing,
    ProviderRefused,
    plan_add,
    plan_sweep,
)

# A fake hypervisor: a text file with one VM name per line.
_FAKE = """
import os, pathlib, sys
db = pathlib.Path(os.environ["FAKE_VMS"])
vms = [v for v in db.read_text().split() if v] if db.exists() else []
verb = sys.argv[1]
key = os.environ.get("TESTO_RESOURCE_ID", "")
if verb == "list":
    print("\\n".join(vms))
elif verb == "create":
    if key == "qasight-vm-bad":
        sys.exit("quota exceeded")
    db.write_text("\\n".join(vms + [key]))
elif verb == "destroy":
    db.write_text("\\n".join(v for v in vms if v != key))
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "fake_vms.py"
    script.write_text(_FAKE, encoding="utf-8")
    run = f'"{sys.executable}" "{script}"'
    (tmp_path / "qasight.yaml").write_text(
        json.dumps(
            {
                "resources": {
                    "vms": {
                        "items": ["vm-1"],
                        "provider": {
                            "list": f"{run} list",
                            "create": f"{run} create",
                            "destroy": f"{run} destroy",
                            "allow": ["create", "destroy"],
                            "max_items": 4,
                            "name_prefix": "qasight-vm-",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "vms.txt").write_text("vm-1", encoding="utf-8")
    monkeypatch.setenv("FAKE_VMS", str(tmp_path / "vms.txt"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'history.db'}")
    for name in (
        "TESTO_SERVER_URL",
        "TESTO_LEASE_DB_URL",
        "TESTO_RESOURCE_SPEC",
        "TESTO_PROVIDER_ACTIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    from qasight.repository.db_config import reset_engine_cache

    reset_engine_cache()
    return tmp_path


def _vms(project: Path) -> list[str]:
    return (project / "vms.txt").read_text().split()


def _pool(**provider: object) -> object:
    return parse_pool(
        "vms",
        {
            "items": ["vm-1"],
            "provider": {
                "list": "x",
                "create": "c",
                "destroy": "d",
                "allow": ["create", "destroy"],
                "max_items": 3,
                "name_prefix": "qasight-vm-",
                **provider,
            },
        },
    )


def test_add_names_keys_from_the_prefix_within_max_items() -> None:
    pool = _pool()
    plan = plan_add(pool, Listing(keys=("vm-1", "qasight-vm-1")), count=2)  # type: ignore[arg-type]
    assert [s.key for s in plan.steps] == ["qasight-vm-2"]
    assert plan.refused == (("qasight-vm-3", "max_items 3 reached"),)


def test_add_refuses_names_without_the_prefix_and_existing_ones() -> None:
    plan = plan_add(_pool(), Listing(keys=("qasight-vm-1",)), names=["vm-9", "qasight-vm-1"])  # type: ignore[arg-type]
    assert not plan.steps
    assert [why for _, why in plan.refused] == [
        "doesn't start with name_prefix 'qasight-vm-'",
        "already exists",
    ]


def test_nothing_runs_when_not_allowed_switched_off_or_list_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ProviderRefused, match="doesn't allow 'create'"):
        plan_add(_pool(allow=["destroy"]), Listing(), count=1)  # type: ignore[arg-type]
    with pytest.raises(ProviderRefused, match="not guessing"):
        plan_add(_pool(), Listing(problem="provider.list failed: exit 1"), count=1)  # type: ignore[arg-type]
    with pytest.raises(ProviderRefused, match=f"at most {MAX_KEYS_PER_COMMAND}"):
        plan_add(_pool(max_items=50), Listing(), count=MAX_KEYS_PER_COMMAND + 1)  # type: ignore[arg-type]
    monkeypatch.setenv("TESTO_PROVIDER_ACTIONS", "0")
    with pytest.raises(ProviderRefused, match="TESTO_PROVIDER_ACTIONS=0"):
        plan_add(_pool(), Listing(), count=1)  # type: ignore[arg-type]


def test_sweep_only_takes_prefixed_keys_outside_the_pool() -> None:
    listing = Listing(keys=("vm-1", "other-vm", *(f"qasight-vm-{i}" for i in range(1, 8))))
    plan = plan_sweep(_pool(), listing, ())  # type: ignore[arg-type]
    assert [s.key for s in plan.steps] == [f"qasight-vm-{i}" for i in range(1, 6)]
    assert plan.note == "2 more after these; run sweep again."


def test_add_is_a_dry_run_until_apply(project: Path) -> None:
    runner = CliRunner()
    dry = runner.invoke(app, ["pool", "add", "vms", "--count", "2"])
    assert dry.exit_code == 0, dry.output
    assert "would run create for qasight-vm-1" in dry.stdout and "Dry run" in dry.stdout
    assert _vms(project) == ["vm-1"]
    assert "No provider commands" in runner.invoke(app, ["pool", "log"]).stdout

    # --apply asks first; "no" runs nothing.
    assert runner.invoke(app, ["pool", "add", "vms", "-n", "1", "--apply"], input="n\n").exit_code
    assert _vms(project) == ["vm-1"]

    done = runner.invoke(app, ["pool", "add", "vms", "-n", "2", "--apply", "--yes"])
    assert done.exit_code == 0, done.output
    assert _vms(project) == ["vm-1", "qasight-vm-1", "qasight-vm-2"]
    assert "Add qasight-vm-1, qasight-vm-2 to resources.vms.items" in done.stdout

    # max_items 4: one more fits.
    capped = runner.invoke(app, ["pool", "add", "vms", "-n", "2", "--apply", "--yes"])
    assert "max_items 4 reached" in capped.stdout
    assert _vms(project)[-1] == "qasight-vm-3" and len(_vms(project)) == 4

    log = json.loads(runner.invoke(app, ["pool", "log", "vms", "--json"]).stdout)
    assert [(a["item"], a["verb"], a["ok"]) for a in log] == [
        ("qasight-vm-3", "create", True),
        ("qasight-vm-2", "create", True),
        ("qasight-vm-1", "create", True),
    ]


def test_a_failing_create_is_logged_and_fails_the_command(project: Path) -> None:
    result = CliRunner().invoke(app, ["pool", "add", "vms", "qasight-vm-bad", "--apply", "--yes"])
    assert result.exit_code == 1
    log = json.loads(CliRunner().invoke(app, ["pool", "log", "--json"]).stdout)
    assert log[0]["ok"] is False and "quota exceeded" in log[0]["detail"]


def test_remove_and_sweep_never_touch_pool_items_or_held_keys(project: Path) -> None:
    (project / "vms.txt").write_text("vm-1 qasight-vm-1 qasight-vm-2", encoding="utf-8")
    runner = CliRunner()
    refused = runner.invoke(app, ["pool", "remove", "vms", "vm-1", "--apply", "--yes"])
    assert refused.exit_code == 1
    assert "in the pool's items" in refused.stdout and _vms(project)[0] == "vm-1"

    # Someone holds qasight-vm-2 (say it was in items when they took it).
    from qasight.resources.client import backend_from_env, default_holder

    backend_from_env().take(parse_pool("vms", {"items": ["qasight-vm-2"]}), holder=default_holder())

    dry = runner.invoke(app, ["pool", "sweep", "vms"])
    assert "would run destroy for qasight-vm-1" in dry.stdout and "holds it" in dry.stdout
    swept = runner.invoke(app, ["pool", "sweep", "vms", "--apply", "--yes"])
    assert swept.exit_code == 0, swept.output
    assert _vms(project) == ["vm-1", "qasight-vm-2"]
