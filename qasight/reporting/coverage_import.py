"""Read a code coverage report into one number per run: covered lines out of all lines.

``qasight run --coverage`` and ``qasight ingest --coverage`` store it on the run, so
coverage has a history next to the test results and the pull request comment can
say how a change moved it. The format is told by the content, not the file name:

- **Cobertura XML** (``coverage.py``'s ``coverage xml``, ``pytest-cov``, Istanbul's
  ``cobertura`` reporter, .NET coverlet): ``<coverage lines-valid lines-covered>``;
- **JaCoCo XML** (Java, Kotlin): the report's ``LINE`` counter;
- **LCOV** (Istanbul/Jest/Vitest ``lcov.info``, ``genhtml`` input): ``LF:``/``LH:``;
- **Go cover profiles** (``go test -coverprofile``): statements, as ``go tool cover``
  counts them.

Several reports (one per package or service) are added together. Only the totals
are kept: per-file coverage is the coverage tool's job.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405 - parsed with forbidden DTDs below
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 50 * 1024 * 1024
# Run-record keys (strings, like every other uploaded metadata value).
METADATA_KEYS: tuple[str, ...] = ("coverage_pct", "coverage_covered", "coverage_total")


class CoverageFormatError(ValueError):
    """The file is not a coverage report this module can read."""


@dataclass(frozen=True)
class CoverageSummary:
    covered: int
    total: int
    formats: tuple[str, ...] = ()

    @property
    def pct(self) -> float:
        return round(100.0 * self.covered / self.total, 2) if self.total else 0.0

    def as_metadata(self) -> dict[str, str]:
        return {
            "coverage_pct": f"{self.pct:.2f}",
            "coverage_covered": str(self.covered),
            "coverage_total": str(self.total),
        }


def _xml_root(text: str) -> ET.Element:
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        # Cobertura files may carry a DOCTYPE line; entities are never needed.
        text = re.sub(r"<!DOCTYPE[^>\[]*(\[[^\]]*\])?\s*>", "", text, count=1)
        if "<!ENTITY" in text:
            raise CoverageFormatError("XML entities are not allowed in a coverage report")
    try:
        return ET.fromstring(text)  # noqa: S314 - no DTD or entities left (checked above)
    except ET.ParseError as exc:
        raise CoverageFormatError(f"not well-formed XML ({exc})") from exc


def _int(value: str | None) -> int:
    try:
        return max(0, int(float(value or 0)))
    except ValueError:
        return 0


def _cobertura(root: ET.Element) -> tuple[int, int]:
    valid, covered = root.get("lines-valid"), root.get("lines-covered")
    if valid is not None and covered is not None:
        return _int(covered), _int(valid)
    # Older writers leave the totals out: count the <line hits=> elements.
    lines = root.findall(".//line")
    return sum(1 for ln in lines if _int(ln.get("hits")) > 0), len(lines)


def _jacoco(root: ET.Element) -> tuple[int, int]:
    for counter in root.findall("counter"):  # the report-level totals
        if counter.get("type") == "LINE":
            missed, covered = _int(counter.get("missed")), _int(counter.get("covered"))
            return covered, missed + covered
    covered = missed = 0
    for counter in root.iter("counter"):
        if counter.get("type") == "LINE":
            missed += _int(counter.get("missed"))
            covered += _int(counter.get("covered"))
    return covered, missed + covered


def _lcov(text: str) -> tuple[int, int]:
    found = hit = 0
    for line in text.splitlines():
        if line.startswith("LF:"):
            found += _int(line[3:].strip())
        elif line.startswith("LH:"):
            hit += _int(line[3:].strip())
    return hit, found


_GO_LINE = re.compile(r"^(?P<block>\S+:\d+\.\d+,\d+\.\d+) (?P<stmts>\d+) (?P<count>\d+)$")


def _go_profile(text: str) -> tuple[int, int]:
    # The same block can appear once per test binary (``-coverpkg``): count it once.
    blocks: dict[str, tuple[int, bool]] = {}
    for line in text.splitlines()[1:]:
        m = _GO_LINE.match(line.strip())
        if not m:
            continue
        stmts, hit = int(m["stmts"]), int(m["count"]) > 0
        seen = blocks.get(m["block"])
        blocks[m["block"]] = (stmts, hit or (seen is not None and seen[1]))
    return sum(s for s, hit in blocks.values() if hit), sum(s for s, _ in blocks.values())


def parse_coverage_text(text: str) -> tuple[str, int, int]:
    """``(format, covered, total)`` for one report's content."""
    head = text.lstrip()[:4096]
    if head.startswith("mode:"):
        return ("go", *_go_profile(text))
    if re.search(r"^(SF|LF|LH):", text, re.MULTILINE) and not head.startswith("<"):
        return ("lcov", *_lcov(text))
    if head.startswith("<"):
        root = _xml_root(text)
        if root.tag == "coverage":
            return ("cobertura", *_cobertura(root))
        if root.tag == "report":
            return ("jacoco", *_jacoco(root))
        raise CoverageFormatError(f"unknown coverage XML root <{root.tag}>")
    raise CoverageFormatError("not Cobertura, JaCoCo, LCOV or a Go cover profile")


def parse_coverage(paths: Iterable[Path]) -> CoverageSummary:
    """Add up every report in ``paths``. Raises :class:`CoverageFormatError` on a bad file."""
    covered = total = 0
    formats: list[str] = []
    for path in paths:
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise CoverageFormatError(f"{path} is larger than 50 MB")
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise CoverageFormatError(f"cannot read {path} ({exc})") from exc
        try:
            kind, c, t = parse_coverage_text(text)
        except CoverageFormatError as exc:
            raise CoverageFormatError(f"{path}: {exc}") from exc
        covered += c
        total += t
        if kind not in formats:
            formats.append(kind)
    return CoverageSummary(covered=covered, total=total, formats=tuple(formats))


def clean_coverage_metadata(raw: Mapping[str, str | None]) -> dict[str, str]:
    """The coverage keys of an upload, kept only when they are consistent numbers."""
    try:
        covered = int(raw.get("coverage_covered") or "")
        total = int(raw.get("coverage_total") or "")
    except ValueError:
        return {}
    if total <= 0 or not 0 <= covered <= total:
        return {}
    return CoverageSummary(covered=covered, total=total).as_metadata()
