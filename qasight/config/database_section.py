"""Read/write optional ``database:`` settings in qasight config files.

Keeps :mod:`qasight.repository.db_config` free of full plan-schema imports.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml

from qasight.config.errors import ConfigDiscoveryError, ConfigValidationError
from qasight.paths import CONFIG_FILE_NAMES, PYPROJECT_TABLES


def _config_candidates(cwd: Path) -> list[Path]:
    cwd = cwd.expanduser().resolve()
    return [*(cwd / name for name in CONFIG_FILE_NAMES), cwd / "pyproject.toml"]


def discover_config_path(*, cwd: Path | None = None) -> Path | None:
    """First existing config file among YAML / pyproject (same order as the loader)."""
    base = (cwd or Path.cwd()).expanduser().resolve()
    for p in _config_candidates(base):
        if p.is_file():
            return p
    return None


def load_raw_config_dict(path: Path) -> dict[str, Any]:
    """Load top-level mapping from YAML or ``[tool.qasight]`` TOML."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ConfigDiscoveryError(f"config file not found: {path}")
    if path.name == "pyproject.toml":
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigValidationError(f"invalid pyproject.toml at {path}: {exc}") from exc
        raw = next((data["tool"][n] for n in PYPROJECT_TABLES if n in data.get("tool", {})), None)
        if raw is None:
            raise ConfigDiscoveryError(f"pyproject.toml at {path} has no [tool.qasight] table.")
        if not isinstance(raw, dict):
            raise ConfigValidationError("[tool.qasight] must be a table.")
        return raw
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(f"cannot read {path}: {exc}") from exc
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigValidationError(f"invalid YAML in {path}: {exc}") from exc
    if parsed is None:
        raise ConfigValidationError(f"config file {path} is empty.")
    if not isinstance(parsed, dict):
        raise ConfigValidationError(f"top-level of {path} must be a mapping.")
    return parsed


def extract_database_url_from_mapping(raw: Mapping[str, Any]) -> str | None:
    """Return ``database.url`` if present and non-empty."""
    db = raw.get("database")
    if not isinstance(db, Mapping):
        return None
    url = db.get("url")
    if url is None:
        return None
    s = str(url).strip()
    return s or None


def database_url_from_discovered_config(*, cwd: Path | None = None) -> str | None:
    """Load the first discovered config and return ``database.url`` when set."""
    path = discover_config_path(cwd=cwd)
    if path is None:
        return None
    try:
        raw = load_raw_config_dict(path)
    except (ConfigDiscoveryError, ConfigValidationError):
        return None
    return extract_database_url_from_mapping(raw)


def build_postgresql_url(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    database: str,
    schema: str | None = None,
) -> str:
    """Compose a SQLAlchemy PostgreSQL URL (password URL-encoded)."""
    user_enc = quote_plus(username)
    pwd_enc = quote_plus(password)
    db_enc = quote_plus(database)
    base = f"postgresql+psycopg://{user_enc}:{pwd_enc}@{host}:{port}/{db_enc}"
    if schema and str(schema).strip():
        sep = "&" if "?" in base else "?"
        base = f"{base}{sep}options=-csearch_path%3D{quote_plus(str(schema).strip())}"
    return base


def merge_database_url_yaml(*, path: Path, url: str) -> None:
    """Merge ``database: {{ url: ... }}`` into a YAML file (round-trip may reorder keys)."""
    path = path.expanduser().resolve()
    raw = load_raw_config_dict(path) if path.is_file() else {}
    if not isinstance(raw, dict):
        raw = {}
    db = raw.get("database")
    if not isinstance(db, dict):
        db = {}
    db["url"] = url
    raw["database"] = db
    try:
        path.write_text(
            yaml.safe_dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ConfigValidationError(f"cannot write {path}: {exc}") from exc


def merge_database_url_pyproject(*, path: Path, url: str) -> None:
    """Merge ``database.url`` under ``[tool.qasight]`` (requires ``tomli_w``)."""
    try:
        import tomli_w
    except ImportError as exc:  # pragma: no cover - exercised when db extra missing
        raise ConfigValidationError(
            "Writing database settings to pyproject.toml requires the `tomli-w` package. "
            "Install qasight with the `db` extra, or use a YAML qasight config."
        ) from exc

    path = path.expanduser().resolve()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigValidationError(f"cannot read or parse {path}: {exc}") from exc

    tool = data.setdefault("tool", {})
    table_name = next((n for n in PYPROJECT_TABLES if n in tool), PYPROJECT_TABLES[0])
    qasight = tool.setdefault(table_name, {})
    if not isinstance(qasight, dict):
        raise ConfigValidationError("[tool.qasight] must be a table.")
    db = qasight.setdefault("database", {})
    if not isinstance(db, dict):
        raise ConfigValidationError("[tool.qasight.database] must be a table.")
    db["url"] = url
    qasight["database"] = db
    tool[table_name] = qasight
    data["tool"] = tool

    try:
        path.write_text(tomli_w.dumps(data), encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(f"cannot write {path}: {exc}") from exc
