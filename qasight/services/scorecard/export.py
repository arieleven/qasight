"""A scorecard in the formats other tools read, with no server needed:

- :func:`to_sarif`: SARIF 2.1.0 for GitHub code scanning (and any SARIF viewer), so the
  findings show on the pull request's diff and in the Security tab;
- :func:`to_html`: one self-contained page to keep as a CI artifact or publish;
- :func:`badge`: a README badge (``quality | B 87``), as SVG or a shields.io endpoint.

All read a :class:`~qasight.services.scorecard.report.Scorecard`, so they show the same
numbers as ``qasight scorecard show`` and the UI.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import escape
from typing import Any

from qasight.services.badge import Badge, render_svg
from qasight.services.scorecard.report import Scorecard, ToolScore, coverage_sentence

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
_SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}
# GitHub sorts security alerts by this (CVSS-like) number.
_SECURITY_SEVERITY = {
    "critical": "9.5",
    "high": "7.5",
    "medium": "5.0",
    "low": "2.0",
    "info": "0.0",
}
_LOCATION = re.compile(r"^(?P<path>.+?)(?::(?P<line>\d+))?(?::(?P<col>\d+))?$")

GRADE_COLORS = {
    "A": "#4c1",
    "B": "#97ca00",
    "C": "#dfb317",
    "D": "#fe7d37",
    "E": "#e05d44",
}


def _location(raw: str) -> dict[str, Any] | None:
    match = _LOCATION.match(raw.strip()) if raw else None
    if not match:
        return None
    path = match.group("path").lstrip("./")
    if not path or "://" in path or " " in path:
        return None
    physical: dict[str, Any] = {"artifactLocation": {"uri": path}}
    if match.group("line"):
        region: dict[str, int] = {"startLine": max(1, int(match.group("line")))}
        if match.group("col"):
            region["startColumn"] = max(1, int(match.group("col")))
        physical["region"] = region
    return {"physicalLocation": physical}


def _rule_id(tool: ToolScore, title: str) -> str:
    return f"{tool.tool}/{title}"[:255]


def to_sarif(card: Scorecard) -> dict[str, Any]:
    """The kept findings of every switched-on tool as one SARIF log, a run per tool.

    Each tool keeps its worst 50 findings; the counts in ``properties`` cover all of
    them. A finding without a file is reported at the repository root, since code
    scanning needs a location.
    """
    runs: list[dict[str, Any]] = []
    for tool in card.tools:
        if not tool.enabled or not tool.findings:
            continue
        rules: dict[str, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []
        for finding in tool.findings:
            title = finding.get("title") or "finding"
            severity = finding.get("severity") or "medium"
            rule_id = _rule_id(tool, title)
            rules.setdefault(
                rule_id,
                {
                    "id": rule_id,
                    "name": title[:200],
                    "shortDescription": {"text": title[:1000]},
                    "properties": {
                        "tags": [tool.category],
                        "security-severity": _SECURITY_SEVERITY.get(severity, "5.0"),
                    },
                },
            )
            location = _location(finding.get("location") or "") or {
                "physicalLocation": {"artifactLocation": {"uri": "."}}
            }
            results.append(
                {
                    "ruleId": rule_id,
                    "level": _SARIF_LEVEL.get(severity, "warning"),
                    "message": {"text": (finding.get("detail") or title)[:4000]},
                    "locations": [location],
                    "properties": {"severity": severity},
                }
            )
        runs.append(
            {
                "tool": {
                    "driver": {
                        "name": f"QASight scorecard: {tool.name}",
                        "informationUri": "https://github.com/arieleven/qasight",
                        "rules": list(rules.values()),
                    }
                },
                "automationDetails": {"id": f"qasight-scorecard/{tool.tool}/"},
                "results": results,
                "properties": {"score": tool.score, "counts": tool.counts},
            }
        )
    return {"$schema": SARIF_SCHEMA, "version": "2.1.0", "runs": runs}


def badge(card: Scorecard | None, *, label: str = "quality") -> Badge:
    """``quality | B 87``, coloured by grade; grey when nothing is scored."""
    if card is None or card.score is None:
        return Badge(label=label, message="not scored", color="#9f9f9f")
    grade = card.grade or ""
    return Badge(
        label=label,
        message=f"{grade} {card.score}".strip(),
        color=GRADE_COLORS.get(grade, "#9f9f9f"),
    )


def badge_svg(card: Scorecard | None, *, label: str = "quality") -> str:
    return render_svg(badge(card, label=label))


def shields_badge(card: Scorecard | None, *, label: str = "quality") -> dict[str, object]:
    """The badge as a shields.io endpoint (``img.shields.io/endpoint?url=...``)."""
    b = badge(card, label=label)
    return {"schemaVersion": 1, "label": b.label, "message": b.message, "color": b.color}


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --bg: #f7f8fa; --card: #fff; --ink: #1b1f24; --muted: #5b6470; --line: #e3e6ea; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #0f1115; --card: #181b21; --ink: #e6e8eb; --muted: #a3a9b3; --line: #2a2f38; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  h2 {{ font-size: 1.05rem; margin: 28px 0 10px; }}
  .muted {{ color: var(--muted); }}
  .hero {{ display: flex; gap: 20px; align-items: center; background: var(--card);
          border: 1px solid var(--line); border-radius: 14px; padding: 20px; margin-top: 16px; }}
  .grade {{ width: 84px; height: 84px; border-radius: 50%; display: grid; place-items: center;
           font-size: 2.2rem; font-weight: 700; color: #fff; flex: none; }}
  .score {{ font-size: 2rem; font-weight: 700; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }}
  .cat {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px; }}
  .cat b {{ font-size: 1.3rem; display: block; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card);
          border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 8px 10px; border-top: 1px solid var(--line);
           vertical-align: top; }}
  th {{ border-top: 0; font-size: .8rem; color: var(--muted); text-transform: uppercase; }}
  .wrap {{ overflow-x: auto; }}
  details summary {{ cursor: pointer; color: var(--muted); }}
  ul.findings {{ margin: 6px 0 0; padding-left: 18px; font-size: .9rem; }}
  code {{ font-size: .85em; word-break: break-all; }}
  .up {{ color: #2da44e; }} .down {{ color: #cf222e; }}
  footer {{ margin-top: 32px; font-size: .8rem; }}
</style>
</head>
<body>
<main>
<h1>{project} {version}</h1>
<div class="muted">{subtitle}</div>
<div class="hero">
  <div class="grade" style="background:{grade_color}">{grade}</div>
  <div>
    <div class="score">{score}<span class="muted" style="font-size:1rem"> / 100</span> {delta}</div>
    <div class="muted">{coverage}</div>
  </div>
</div>
<h2>Categories</h2>
<div class="grid">{categories}</div>
<h2>Tools</h2>
<div class="wrap"><table>
<thead><tr><th>Tool</th><th>Score</th><th>Summary</th></tr></thead>
<tbody>{tools}</tbody>
</table></div>
<footer class="muted">Written by <a href="https://github.com/arieleven/qasight">QASight</a>
(<code>qasight scorecard show --format html</code>) on {generated}.</footer>
</main>
</body>
</html>
"""


def _delta_html(score: int | None, previous: int | None) -> str:
    if score is None or previous is None or score == previous:
        return ""
    diff = score - previous
    cls = "up" if diff > 0 else "down"
    return f'<span class="{cls}" style="font-size:1rem">{diff:+d}</span>'


def to_html(card: Scorecard) -> str:
    """One self-contained page (no scripts, no external files) for the version."""
    subtitle = (
        f"Compared with {escape(card.previous_version)} ({card.previous_score})"
        if card.previous_version and card.previous_score is not None
        else "First scored version"
    )
    if card.commit_sha:
        subtitle += f" · commit <code>{escape(card.commit_sha[:12])}</code>"
    categories = "".join(
        f'<div class="cat"><span class="muted">{escape(c.name)}</span>'
        f"<b>{'n/a' if c.score is None else c.score} {_delta_html(c.score, c.previous_score)}</b></div>"
        for c in card.categories
    )
    rows = []
    for tool in sorted(card.tools, key=lambda t: (not t.enabled, t.score)):
        findings = ""
        if tool.findings:
            items = "".join(
                f"<li><b>{escape(f.get('severity', ''))}</b> {escape(f.get('title', ''))}"
                + (f" <code>{escape(f['location'])}</code>" if f.get("location") else "")
                + (
                    f"<br><span class='muted'>{escape(f['detail'])}</span>"
                    if f.get("detail")
                    else ""
                )
                + "</li>"
                for f in tool.findings
            )
            findings = (
                f"<details><summary>{len(tool.findings)} findings</summary>"
                f'<ul class="findings">{items}</ul></details>'
            )
        state = "" if tool.enabled else ' <span class="muted">(off)</span>'
        rows.append(
            f"<tr><td>{escape(tool.name)}{state}<br><span class='muted'>{escape(tool.category)}</span></td>"
            f"<td><b>{tool.score}</b> {_delta_html(tool.score, tool.previous_score)}</td>"
            f"<td>{escape(tool.summary)}{findings}</td></tr>"
        )
    grade = card.grade or "?"
    return _PAGE.format(
        title=escape(f"Scorecard: {card.project} {card.version}"),
        project=escape(card.project),
        version=escape(card.version),
        subtitle=subtitle,
        grade=escape(grade),
        grade_color=GRADE_COLORS.get(grade, "#9f9f9f"),
        score="n/a" if card.score is None else card.score,
        delta=_delta_html(card.score, card.previous_score),
        coverage=escape(coverage_sentence(card.coverage)) if card.coverage else "",
        categories=categories,
        tools="".join(rows),
        generated=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )
