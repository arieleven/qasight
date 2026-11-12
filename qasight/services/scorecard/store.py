"""Scorecard results, coverage and tool switches, on the run-history database (``scorecard_result``, ``scorecard_coverage``, ``scorecard_tool``)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from qasight.services.scorecard.parsers import ToolResult
from qasight.services.scorecard.tools import TOOLS, TOOLS_BY_ID

MAX_NAME = 255
MAX_VERSION = 128
# A linter an engine ran: "megalinter:PYTHON_RUFF".
_ENGINE_TOOL = re.compile(r"^megalinter:[A-Z0-9][A-Z0-9_]{0,80}$")


def is_engine_tool(tool: str) -> bool:
    return bool(_ENGINE_TOOL.match(tool))


def known_tool(tool: str) -> bool:
    return tool in TOOLS_BY_ID or is_engine_tool(tool)


class ScorecardError(ValueError):
    """A bad project, version or tool name."""


@dataclass(frozen=True)
class StoredResult:
    project: str
    version: str
    tool: str
    score: int
    summary: str
    metrics: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    findings: list[dict[str, str]] = field(default_factory=list)
    commit_sha: str | None = None
    source: str = "import"
    recorded_at: float = 0.0
    # Set for a linter an engine ran (not in the tool list): its name and category.
    name: str | None = None
    category: str | None = None
    fingerprints: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StoredCoverage:
    """The tools a version's latest scan expected (they fit its languages) and why some didn't run."""

    expected: list[str]
    missing: dict[str, str]
    languages: list[str] = field(default_factory=list)
    recorded_at: float = 0.0


def _id(project: str, version: str, tool: str) -> str:
    return hashlib.sha256(f"{project}\n{version}\n{tool}".encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def clean_names(project: str, version: str, tool: str) -> tuple[str, str, str]:
    project, version, tool = project.strip(), version.strip(), tool.strip()
    if not project or not version:
        raise ScorecardError("a scorecard result needs a project and a version")
    if len(project) > MAX_NAME or len(version) > MAX_VERSION:
        raise ScorecardError(
            f"project is limited to {MAX_NAME} characters and version to {MAX_VERSION}"
        )
    if not known_tool(tool):
        raise ScorecardError(f"unknown tool {tool!r}; known tools: {', '.join(TOOLS_BY_ID)}")
    return project, version, tool


def _stored(row: Any) -> StoredResult:
    details = row.details or {}
    return StoredResult(
        project=row.project,
        version=row.version,
        tool=row.tool,
        score=int(row.score),
        summary=row.summary,
        metrics=dict(details.get("metrics") or {}),
        counts={k: int(v) for k, v in (details.get("counts") or {}).items()},
        findings=list(details.get("findings") or []),
        commit_sha=row.commit_sha,
        source=row.source,
        recorded_at=_aware(row.recorded_at).timestamp(),
        name=details.get("name") or None,
        category=details.get("category") or None,
        fingerprints=[str(f) for f in details.get("fingerprints") or []],
    )


class ScorecardStore:
    """Scorecard rows on a SQLAlchemy engine (the run-history DB by default)."""

    def __init__(self, engine: Any = None) -> None:
        from qasight.repository.db_config import create_db_and_tables, get_engine

        self._engine = engine if engine is not None else get_engine()
        create_db_and_tables(self._engine)

    # --- results ------------------------------------------------------------

    def record(
        self,
        *,
        project: str,
        version: str,
        tool: str,
        result: ToolResult,
        commit_sha: str | None = None,
        source: str = "import",
        name: str | None = None,
        category: str | None = None,
    ) -> StoredResult:
        """Save ``tool``'s result for ``project`` at ``version``, replacing an earlier one."""
        return self.record_raw(
            project=project,
            version=version,
            tool=tool,
            score=result.score,
            summary=result.summary,
            metrics=dict(result.metrics),
            counts={k: v for k, v in result.counts.items() if v},
            findings=[f.to_dict() for f in result.findings],
            commit_sha=commit_sha,
            source=source,
            name=name or result.name,
            category=category or result.category,
            fingerprints=list(result.fingerprints),
        )

    def record_raw(
        self,
        *,
        project: str,
        version: str,
        tool: str,
        score: int,
        summary: str,
        metrics: dict[str, Any] | None = None,
        counts: dict[str, int] | None = None,
        findings: list[dict[str, str]] | None = None,
        commit_sha: str | None = None,
        source: str = "import",
        name: str | None = None,
        category: str | None = None,
        fingerprints: list[str] | None = None,
    ) -> StoredResult:
        from sqlmodel import Session

        from qasight.repository.models import ScorecardResult

        project, version, tool = clean_names(project, version, tool)
        if not 0 <= int(score) <= 100:
            raise ScorecardError("a score is 0 to 100")
        row = ScorecardResult(
            id=_id(project, version, tool),
            project=project,
            version=version,
            tool=tool,
            score=int(score),
            summary=(summary or "")[:500],
            details={
                "metrics": metrics or {},
                "counts": counts or {},
                "findings": [
                    {
                        k: str(f.get(k, ""))[:500]
                        for k in ("severity", "title", "detail", "location")
                    }
                    for f in (findings or [])[:50]
                ],
                **({"name": name[:120]} if name else {}),
                **({"category": category[:32]} if category else {}),
                **(
                    {"fingerprints": [str(f)[:40] for f in fingerprints[:5000]]}
                    if fingerprints
                    else {}
                ),
            },
            commit_sha=(commit_sha or None) and commit_sha[:64],
            source=source[:32],
            recorded_at=datetime.now(tz=UTC),
        )
        with Session(self._engine) as session:
            session.merge(row)
            session.commit()
        return _stored(row)

    def results(self, project: str | None = None, version: str | None = None) -> list[StoredResult]:
        from sqlmodel import Session, col, select

        from qasight.repository.models import ScorecardResult

        query = select(ScorecardResult).order_by(col(ScorecardResult.recorded_at))
        if project:
            query = query.where(ScorecardResult.project == project)
        if version:
            query = query.where(ScorecardResult.version == version)
        with Session(self._engine) as session:
            return [_stored(row) for row in session.exec(query).all()]

    # --- coverage -----------------------------------------------------------

    def record_coverage(
        self,
        *,
        project: str,
        version: str,
        expected: list[str],
        missing: dict[str, str],
        languages: list[str] | None = None,
    ) -> None:
        """Save which tools a scan of ``project`` at ``version`` expected and which didn't run."""
        from sqlmodel import Session

        from qasight.repository.models import ScorecardCoverage

        project, version = project.strip(), version.strip()
        row = ScorecardCoverage(
            id=hashlib.sha256(f"{project}\n{version}".encode()).hexdigest(),
            project=project,
            version=version,
            details={
                "expected": [t for t in expected if known_tool(t)],
                "missing": {t: str(why)[:300] for t, why in missing.items() if known_tool(t)},
                "languages": list(languages or []),
            },
            recorded_at=datetime.now(tz=UTC),
        )
        with Session(self._engine) as session:
            session.merge(row)
            session.commit()

    def coverage(self, project: str, version: str) -> StoredCoverage | None:
        return self.coverages(project).get(version)

    def coverages(self, project: str | None = None) -> dict[str, StoredCoverage]:
        """Version -> its latest scan's coverage, for ``project`` (keyed project\\nversion when None)."""
        from sqlmodel import Session, select

        from qasight.repository.models import ScorecardCoverage

        query = select(ScorecardCoverage)
        if project is not None:
            query = query.where(ScorecardCoverage.project == project)
        with Session(self._engine) as session:
            rows = session.exec(query).all()
        out: dict[str, StoredCoverage] = {}
        for row in rows:
            details = row.details or {}
            key = row.version if project is not None else f"{row.project}\n{row.version}"
            out[key] = StoredCoverage(
                expected=list(details.get("expected") or []),
                missing=dict(details.get("missing") or {}),
                languages=list(details.get("languages") or []),
                recorded_at=_aware(row.recorded_at).timestamp(),
            )
        return out

    def delete_version(self, project: str, version: str) -> int:
        from sqlmodel import Session, select

        from qasight.repository.models import ScorecardResult

        with Session(self._engine) as session:
            rows = session.exec(
                select(ScorecardResult).where(
                    ScorecardResult.project == project, ScorecardResult.version == version
                )
            ).all()
            for row in rows:
                session.delete(row)
            from qasight.repository.models import ScorecardCoverage

            for cov in session.exec(
                select(ScorecardCoverage).where(
                    ScorecardCoverage.project == project, ScorecardCoverage.version == version
                )
            ).all():
                session.delete(cov)
            session.commit()
            return len(rows)

    # --- tool switches ------------------------------------------------------

    def enabled(self) -> dict[str, bool]:
        """Every tool id -> on or off (the stored choice, else the tool's default)."""
        from sqlmodel import Session, select

        from qasight.repository.models import ScorecardToolSetting

        with Session(self._engine) as session:
            stored = {
                row.tool: row.enabled for row in session.exec(select(ScorecardToolSetting)).all()
            }
        out = {tool.id: stored.get(tool.id, tool.default_on) for tool in TOOLS}
        # Linters an engine ran are on unless switched off.
        out.update({k: v for k, v in stored.items() if is_engine_tool(k)})
        return out

    def set_enabled(self, tool: str, enabled: bool) -> None:
        from sqlmodel import Session

        from qasight.repository.models import ScorecardToolSetting

        if not known_tool(tool):
            raise ScorecardError(f"unknown tool {tool!r}; known tools: {', '.join(TOOLS_BY_ID)}")
        with Session(self._engine) as session:
            session.merge(
                ScorecardToolSetting(
                    tool=tool, enabled=bool(enabled), updated_at=datetime.now(tz=UTC)
                )
            )
            session.commit()

    def reset_enabled(self) -> None:
        """Back to every tool's default."""
        from sqlmodel import Session, delete

        from qasight.repository.models import ScorecardToolSetting

        with Session(self._engine) as session:
            session.exec(delete(ScorecardToolSetting))  # type: ignore[call-overload]
            session.commit()
