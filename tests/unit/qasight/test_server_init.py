"""``qasight server init`` (qasight/services/server_setup.py) and the first admin from env."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
import yaml
from sqlmodel import create_engine
from typer.testing import CliRunner

from qasight.accounts import AccountStore, ensure_first_admin
from qasight.cli.app import app
from qasight.services import server_setup

pytestmark = [pytest.mark.unit, pytest.mark.tier_fast]

REPO_COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "server" / "docker-compose.yml"


def _env(folder: Path) -> dict[str, str]:
    lines = (folder / ".env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


def test_the_packaged_compose_file_is_the_repositorys() -> None:
    assert server_setup.TEMPLATE.read_text() == REPO_COMPOSE.read_text(), (
        "copy deploy/server/docker-compose.yml to qasight/templates/server/"
    )


def test_init_writes_compose_and_a_private_env_with_fresh_secrets(tmp_path: Path) -> None:
    folder = tmp_path / "srv"

    result = CliRunner().invoke(app, ["server", "init", "--dir", str(folder)])

    assert result.exit_code == 0, result.output
    compose = yaml.safe_load((folder / "docker-compose.yml").read_text())
    assert "build" not in compose["services"]["server"]
    assert {"server", "backup", "postgres"} <= set(compose["services"])
    env = _env(folder)
    assert len(env["TESTO_API_TOKEN"]) == 64 and len(env["POSTGRES_PASSWORD"]) == 32
    assert "TESTO_AUTH" not in env
    assert stat.S_IMODE((folder / ".env").stat().st_mode) == 0o600
    assert env["TESTO_API_TOKEN"] not in result.output
    assert not (folder / "Caddyfile").exists()

    again = CliRunner().invoke(app, ["server", "init", "--dir", str(folder)])
    assert again.exit_code == 2 and "--force" in again.output
    CliRunner().invoke(app, ["server", "init", "--dir", str(folder), "--force"])
    assert _env(folder)["TESTO_API_TOKEN"] != env["TESTO_API_TOKEN"]


def test_domain_adds_caddy_and_keeps_the_server_port_local(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "server",
            "init",
            "--dir",
            str(tmp_path),
            "--domain",
            "QASight.Example.com",
            "--admin",
            "ada",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "Caddyfile").read_text().splitlines()[2] == "qasight.example.com {"
    override = yaml.safe_load((tmp_path / "docker-compose.override.yml").read_text())
    assert override["services"]["caddy"]["ports"] == ["80:80", "443:443"]
    env = _env(tmp_path)
    assert env["TESTO_SERVER_PORT"] == "127.0.0.1:8000"
    assert (env["TESTO_AUTH"], env["TESTO_ADMIN_USER"]) == ("accounts", "ada")
    assert len(env["TESTO_ADMIN_PASSWORD"]) >= 20
    assert "https://qasight.example.com" in result.output


@pytest.mark.parametrize("args", [["--domain", "not a domain"], ["--admin", "-bad name"]])
def test_bad_input_is_exit_2(tmp_path: Path, args: list[str]) -> None:
    result = CliRunner().invoke(app, ["server", "init", "--dir", str(tmp_path), *args])
    assert result.exit_code == 2
    assert not (tmp_path / ".env").exists()


def test_first_admin_is_created_once_and_only_with_accounts(tmp_path: Path) -> None:
    store = AccountStore(create_engine(f"sqlite:///{tmp_path / 'h.db'}"))
    env = {"TESTO_ADMIN_USER": "ada", "TESTO_ADMIN_PASSWORD": "a-long-password-1"}

    assert ensure_first_admin(env, store=store) is None  # TESTO_AUTH isn't accounts
    env["TESTO_AUTH"] = "accounts"
    assert ensure_first_admin(env, store=store) == "ada"
    assert store.get("ada") is not None and store.get("ada").role == "admin"  # type: ignore[union-attr]
    env["TESTO_ADMIN_USER"] = "bob"
    assert ensure_first_admin(env, store=store) is None
    assert store.get("bob") is None
