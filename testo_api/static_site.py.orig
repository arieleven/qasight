"""Freeze the read-only API into JSON files so the UI runs on a static host.

GitHub or GitLab Pages serve files, not processes, so the FastAPI app cannot be
published there. :class:`Exporter` calls every read-only endpoint the UI uses
*in-process*, through ``TestClient`` against the same ``create_app()`` the real
server runs, and writes each response body under ``<out>/data/``. The UI's
fetch shim (``frontend/src/lib/static-backend.ts``) maps API requests onto
those files, so the payloads are the real ones rather than fixtures.

Two callers:

* ``testo export-site`` (:func:`build_site`): copies the UI bundled in the
  wheel, points its ``<base href>`` at the subpath the site is published under,
  adds the ``testo-static-data`` meta tag that switches the UI to read-only
  mode, and writes the data next to it. One build, any path.
* ``scripts/export_static_site.py``: this repository's own Pages demo, which
  builds the UI itself with ``VITE_STATIC_DATA_BASE``.

Allure/native HTML reports already live under ``static/history/<run_id>/``
after a run; they are copied next to the data so report links resolve.

A static site cannot call an AI provider when a visitor clicks "Generate AI
Summary", so when ``ANTHROPIC_API_KEY`` is set the export generates the summary
for each failed run first, through the same endpoint the button calls, and
freezes the result.

Only cycles that have a run in the export are published: a cycle card with no
history would lead nowhere on a site that cannot start runs.

The layout it writes is the contract shared with ``static-backend.ts``:

    data/manifest.json                     provenance + the exported run ids
    data/health.json                       GET /api/v1/health/ready
    data/ai-config.json                    GET /api/v1/ai/config/status
    data/cycles.json                       GET /api/v1/cycles (cycles that ran)
    data/cycles/<name>.json                GET /api/v1/cycles/{name}
    data/flaky/<name>.json                 GET /api/v1/analytics/flaky?cycle={name}
    data/tests/<name>.json                 GET /api/v1/analytics/tests?cycle={name}
    data/test-history/<name>/<id>.json     GET /api/v1/analytics/test-history?cycle={name}&key={key}
                                           (<id> is test_history.stable_id(key))
    data/resources.json                    GET /api/v1/resources
    data/runs.json                         GET /api/v1/runs
    data/runs/<run_id>/detail.json         GET /api/v1/runs/{id}
    data/runs/<run_id>/reports.json        GET /api/v1/runs/{id}/reports
    data/runs/<run_id>/pyramid.json        GET /api/v1/runs/{id}/pyramid
    data/runs/<run_id>/failures.json       GET /api/v1/runs/{id}/failures
    data/runs/<run_id>/ai-summary.json     GET /api/v1/runs/{id}/ai-summary
    data/dashboard/overview.json           GET /api/v1/dashboard/overview
    data/dashboard/recent-runs.json        GET /api/v1/dashboard/runs/recent
    data/dashboard/attention.json          GET /api/v1/dashboard/attention
    data/project-health/all.json           GET /api/v1/project-health
    data/project-health/cycles/<name>.json GET /api/v1/project-health?cycle={name}
    data/project-health/skills.json        GET /api/v1/project-health/skills
    data/project-health/ai-review.json     GET /api/v1/project-health/ai-review
    data/project-health/ai-review/<name>.json  GET /api/v1/project-health/ai-review?cycle={name}
    data/delta/<current>__<baseline>/comparison.json
    data/delta/<current>__<baseline>/cases.json
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

# Model for the frozen AI summaries; override with TESTO_DEMO_AI_MODEL. The
# Anthropic provider sends a sampling temperature and reads the first content
# block, so it needs a model without always-on thinking.
DEFAULT_DEMO_AI_MODEL = "claude-haiku-4-5"

# What `testo export-site` adds to the bundled UI's index.html: the UI reads it
# (static-backend.ts) and answers API calls from data/ instead of a server.
STATIC_DATA_META = '<meta name="testo-static-data" content="data/" />'
_BASE_TAG = re.compile(r'<base href="[^"]*"\s*/?>')


def _provenance() -> dict[str, Any]:
    """Commit, branch and CI run of the export, from GitHub Actions, GitLab CI or git."""
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    github_run = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None
    return {
        "commit": os.getenv("CI_COMMIT_SHA")
        or os.getenv("GITHUB_SHA")
        or _git("rev-parse", "HEAD"),
        "commit_ref": os.getenv("CI_COMMIT_REF_NAME")
        or os.getenv("GITHUB_REF_NAME")
        or _git("rev-parse", "--abbrev-ref", "HEAD"),
        "pipeline_url": os.getenv("CI_PIPELINE_URL") or github_run,
        "project_url": os.getenv("CI_PROJECT_URL") or (f"{server}/{repo}" if repo else None),
    }


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=Path.cwd(), capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _scrub(payload: Any, root: str) -> Any:
    """Make checkout-absolute paths relative before they are published.

    Several payloads echo filesystem paths (the resolved ``testosterone.yaml``,
    artifact snapshot dirs). On a public site those would expose the CI runner's
    directory layout and read as noise, so the checkout root is stripped.
    """
    if isinstance(payload, str):
        return payload.replace(root + "/", "").replace(root, ".")
    if isinstance(payload, list):
        return [_scrub(item, root) for item in payload]
    if isinstance(payload, dict):
        return {key: _scrub(value, root) for key, value in payload.items()}
    return payload


def _ran_cycles_only(listing: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep the cycles that have at least one exported run, in config order."""
    ran = {run.get("cycle") for run in runs}
    return {**listing, "items": [c for c in listing.get("items", []) if c.get("name") in ran]}


class Exporter:
    """Writes one JSON file per read-only endpoint the UI calls."""

    def __init__(
        self,
        *,
        out_dir: Path,
        site_url: str,
        run_limit: int,
        delta_pairs: int,
        demo_resources: bool = False,
        demo: bool = False,
        scrub_root: Path | None = None,
    ) -> None:
        from fastapi.testclient import TestClient

        # The export switches AI on for its own run; keep that out of the local settings file.
        os.environ["TESTO_AI_SETTINGS_PATH"] = ""
        from testo_api.main import create_app

        self.out_dir = out_dir
        self.data_dir = out_dir / "data"
        self.site_url = site_url.rstrip("/")
        self.run_limit = run_limit
        self.delta_pairs = delta_pairs
        self.demo_resources = demo_resources
        self.demo = demo
        self.scrub_root = str((scrub_root or Path.cwd()).resolve())
        self.client = TestClient(create_app(), base_url="http://localhost")
        self.written: list[str] = []
        self.skipped: list[str] = []
        self.ai_summaries = 0

    # --- plumbing -----------------------------------------------------------

    def _get(self, path: str) -> Any | None:
        """GET ``path``, returning the decoded body, or None when it is unusable.

        ``/health/ready`` answers 503 when a check is degraded while still carrying
        its payload, which is exactly what the UI renders, so non-2xx bodies are
        kept when they parse.
        """
        resp = self.client.get(path)
        try:
            body = resp.json()
        except ValueError:
            self.skipped.append(f"{path} (status {resp.status_code}, non-JSON body)")
            return None
        if resp.status_code >= 400 and not isinstance(body, dict):
            self.skipped.append(f"{path} (status {resp.status_code})")
            return None
        if resp.status_code >= 400 and "error" in body:
            self.skipped.append(f"{path} (status {resp.status_code}: {body['error'].get('code')})")
            return None
        return body

    def _write(self, rel_path: str, payload: Any) -> None:
        target = self.data_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(_scrub(payload, self.scrub_root), indent=2, sort_keys=True) + "\n"
        target.write_text(text, encoding="utf-8")
        self.written.append(rel_path)

    def _dump(self, api_path: str, rel_path: str) -> Any | None:
        payload = self._get(api_path)
        if payload is None:
            return None
        self._write(rel_path, payload)
        return payload

    # --- endpoint groups ----------------------------------------------------

    def export_runs(self) -> list[dict[str, Any]]:
        listing = self._get(f"/api/v1/runs?limit={self.run_limit}")
        items: list[dict[str, Any]] = list(listing.get("items", [])) if listing else []
        items = items[: self.run_limit]
        self._write("runs.json", {"items": items})

        failed = [item["run_id"] for item in items if item.get("returncode") not in (0, None)]
        self.generate_ai_summaries(failed)

        for item in items:
            run_id = item["run_id"]
            base = f"runs/{run_id}"
            self._dump(f"/api/v1/runs/{run_id}", f"{base}/detail.json")
            self._dump(f"/api/v1/runs/{run_id}/reports", f"{base}/reports.json")
            self._dump(f"/api/v1/runs/{run_id}/pyramid", f"{base}/pyramid.json")
            self._dump(f"/api/v1/runs/{run_id}/failures", f"{base}/failures.json")
            self._dump(f"/api/v1/runs/{run_id}/ai-summary", f"{base}/ai-summary.json")
        return items

    def generate_ai_summaries(self, run_ids: list[str]) -> None:
        """Generate (or reuse) the AI failure summary of each run, if a key is set.

        Goes through the API like the UI's "Generate AI summary" button does:
        enable the Anthropic provider with the key from the environment, then
        ask for each run's summary. The key itself is never written anywhere;
        only the summary text ends up in ``ai-summary.json``.
        """
        if not run_ids or not os.getenv("ANTHROPIC_API_KEY"):
            return
        resp = self.client.put(
            "/api/v1/ai/config",
            json={
                "enabled": True,
                "provider": "anthropic",
                "model": os.getenv("TESTO_DEMO_AI_MODEL") or DEFAULT_DEMO_AI_MODEL,
                "api_key_source": "env",
                # One attempt per run, bounded, so a slow provider cannot stall the deploy.
                "timeout_s": 30,
                "retry_count": 0,
            },
        )
        if resp.status_code != 200:
            self.skipped.append(f"AI summaries (config rejected: status {resp.status_code})")
            return
        for run_id in run_ids:
            # Optional feature: whatever goes wrong here must not stop the site from publishing.
            try:
                summary = self.client.post(
                    f"/api/v1/runs/{run_id}/ai-summary:generate", json={"force_refresh": False}
                ).json()
            except Exception as exc:  # noqa: BLE001
                self.skipped.append(f"AI summary for {run_id} ({type(exc).__name__})")
                continue
            if summary.get("status") == "available":
                self.ai_summaries += 1
            else:
                self.skipped.append(f"AI summary for {run_id} ({summary.get('error_code')})")

    def export_cycles(self, runs: list[dict[str, Any]]) -> None:
        listing = self._get("/api/v1/cycles")
        if listing is None:
            return
        listing = _ran_cycles_only(listing, runs)
        self._write("cycles.json", listing)
        for cycle in listing["items"]:
            name = cycle["name"]
            self._dump(f"/api/v1/cycles/{quote(name, safe='')}", f"cycles/{name}.json")
            self._dump(
                f"/api/v1/analytics/flaky?cycle={quote(name, safe='')}", f"flaky/{name}.json"
            )
            self.export_tests(name)
            query = f"?cycle={quote(name, safe='')}"
            self._dump(f"/api/v1/project-health{query}", f"project-health/cycles/{name}.json")
            self._dump(
                f"/api/v1/project-health/ai-review{query}", f"project-health/ai-review/{name}.json"
            )

    def export_tests(self, cycle: str) -> None:
        """The cycle's test list, and one history file per test for the per-test page."""
        from testo_core.services.test_history import stable_id

        listing = self._dump(
            f"/api/v1/analytics/tests?cycle={quote(cycle, safe='')}", f"tests/{cycle}.json"
        )
        for item in (listing or {}).get("items", []):
            key = str(item["key"])
            self._dump(
                f"/api/v1/analytics/test-history?cycle={quote(cycle, safe='')}"
                f"&key={quote(key, safe='')}",
                f"test-history/{cycle}/{stable_id(key)}.json",
            )

    def export_dashboard(self) -> None:
        overview = self._get("/api/v1/dashboard/overview?recent_limit=6")
        if overview is not None:
            self._write("dashboard/overview.json", self._absolutize_report_links(overview))
        self._dump("/api/v1/dashboard/runs/recent?limit=8", "dashboard/recent-runs.json")
        self._dump("/api/v1/dashboard/attention", "dashboard/attention.json")
        self._dump("/api/v1/project-health", "project-health/all.json")
        self._dump("/api/v1/project-health/skills", "project-health/skills.json")
        self._dump("/api/v1/project-health/ai-review", "project-health/ai-review.json")

    def export_deltas(self, runs: list[dict[str, Any]]) -> list[list[str]]:
        """Export the run pairs the UI can ask for.

        The Dashboard and Runs pages only ever compare a run with the previous
        run of the same cycle (this run of ``fake-api`` against the previous
        one), so those are the pairs exported, newest first across cycles.
        """
        pairs: list[tuple[str, str]] = []
        previous_by_cycle: dict[str | None, str] = {}
        for run in reversed(runs):  # oldest first
            cycle = run.get("cycle")
            if cycle in previous_by_cycle:
                pairs.append((run["run_id"], previous_by_cycle[cycle]))
            previous_by_cycle[cycle] = run["run_id"]
        pairs.reverse()

        exported: list[list[str]] = []
        for current, baseline in pairs[: self.delta_pairs]:
            folder = f"delta/{current}__{baseline}"
            comparison = self._dump(
                f"/api/v1/analytics/delta?current_run_id={current}&baseline_run_id={baseline}",
                f"{folder}/comparison.json",
            )
            if comparison is None:
                continue
            self._dump(
                f"/api/v1/analytics/delta/cases?current_run_id={current}&baseline_run_id={baseline}",
                f"{folder}/cases.json",
            )
            exported.append([current, baseline])
        return exported

    def export_misc(self) -> None:
        self._dump("/api/v1/health/ready", "health.json")
        self._dump("/api/v1/ai/config/status", "ai-config.json")
        if self.demo_resources:
            self.seed_demo_resources()
        self._dump("/api/v1/resources", "resources.json")

    def seed_demo_resources(self) -> None:
        """Made-up pools and leases (``testo lease demo``), so the Resources page has content.

        The demo's runs never lease keys, so without this the page would be empty.
        """
        from testo_core.resources.demo import seed_demo
        from testo_core.resources.store import LeaseStore

        result = seed_demo(LeaseStore())
        print(f"[export] demo resources: {result.held} held, {result.waiting} waiting")

    # --- report HTML --------------------------------------------------------

    def copy_reports(self, runs: list[dict[str, Any]]) -> int:
        """Copy ``static/history/<run_id>/`` for each exported run into the site."""
        from testo_core.paths import STATIC_HISTORY_ROOT

        copied = 0
        for item in runs:
            src = Path(STATIC_HISTORY_ROOT) / item["run_id"]
            if not src.is_dir():
                self.skipped.append(f"report html for {item['run_id']} (no {src})")
                continue
            dst = self.out_dir / "history" / item["run_id"]
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied += 1
        return copied

    def _absolutize_report_links(self, overview: dict[str, Any]) -> dict[str, Any]:
        """Make dashboard report links absolute.

        The dashboard hands the UI paths relative to the API's ``/history`` mount
        and the UI uses them verbatim in ``href``, which on a static host would
        resolve against whatever page the user is on. Rewriting them against the
        published site URL keeps those links working from every route.
        """
        if not self.site_url:
            return overview
        links = overview.get("report_links") or {}
        for link in links.values():
            url = link.get("url") if isinstance(link, dict) else None
            if isinstance(url, str) and url.startswith("history/"):
                link["url"] = f"{self.site_url}/{url}"
        return overview

    # --- entry point --------------------------------------------------------

    def run(self) -> int:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Before export_runs(): generating AI summaries switches the in-process
        # provider on, and the published settings should describe the snapshot.
        self.export_misc()
        runs = self.export_runs()
        if not runs:
            print(
                "error: no runs in the history database: record one first "
                "(testo run, testo ingest or pytest --testo).",
                file=sys.stderr,
            )
            return 1
        self.export_cycles(runs)
        self.export_dashboard()
        deltas = self.export_deltas(runs)
        copied = self.copy_reports(runs)

        self._write(
            "manifest.json",
            {
                "mode": "static",
                "generated_at": time.time(),
                "site_url": self.site_url or None,
                "run_ids": [r["run_id"] for r in runs],
                "latest_run_id": runs[0]["run_id"],
                "delta_pairs": deltas,
                "demo": self.demo,
                **_provenance(),
            },
        )

        print(f"[export] {len(self.written)} JSON files under {self.data_dir}")
        print(
            f"[export] {len(runs)} run(s), {len(deltas)} comparison(s), {copied} report tree(s), "
            f"{self.ai_summaries} AI summary(ies)"
        )
        for note in self.skipped:
            print(f"[export] skipped: {note}")
        return 0


class SiteError(Exception):
    """The site cannot be built (no bundled UI, or a UI build that can't move)."""


@dataclass(frozen=True)
class SiteResult:
    out_dir: Path
    base_path: str
    runs: int


def normalize_base_path(base_path: str | None, site_url: str | None = None) -> str:
    """``/repo/`` style: from ``--base-path``, else the path of ``--site-url``, else ``/``."""
    raw = (base_path or "").strip()
    if not raw and site_url:
        raw = urlparse(site_url).path
    raw = "/" + raw.strip("/")
    return raw if raw == "/" else raw + "/"


def github_pages_base_path(repository: str) -> str:
    """Where GitHub Pages serves ``owner/name``: ``/`` for ``owner.github.io``, else ``/name/``."""
    owner, _, name = repository.partition("/")
    if name.lower() == f"{owner}.github.io".lower():
        return "/"
    return f"/{name}/"


def prepare_index_html(html: str, base_path: str) -> str:
    """Point the bundled UI at ``base_path`` and switch it to read-only static mode."""
    if not _BASE_TAG.search(html):
        raise SiteError(
            "this UI build has no <base href> (it was built for one fixed path); "
            "use the UI bundled in a released testo-core, or one built by scripts/build_ui_bundle.sh"
        )
    html = _BASE_TAG.sub(f'<base href="{base_path}" />', html, count=1)
    if STATIC_DATA_META not in html:
        html = html.replace("<head>", "<head>\n    " + STATIC_DATA_META, 1)
    return html


def build_site(
    out_dir: Path,
    *,
    ui_dir: Path,
    base_path: str = "/",
    site_url: str = "",
    run_limit: int = 10,
    delta_pairs: int = 20,
) -> SiteResult:
    """Write a read-only site: the bundled UI, the exported data and the run reports.

    ``out_dir`` is replaced. GitHub Pages serves ``404.html`` for unknown paths,
    which is how a deep link like ``/runs/<id>`` survives a refresh, so the
    index is copied there too; ``.nojekyll`` keeps Pages from skipping files.
    """
    index = ui_dir / "index.html"
    if not index.is_file():
        raise SiteError(f"no UI build at {ui_dir}")
    html = prepare_index_html(index.read_text(encoding="utf-8"), base_path)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(ui_dir, out_dir)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "404.html").write_text(html, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    exporter = Exporter(
        out_dir=out_dir,
        site_url=site_url,
        run_limit=max(1, run_limit),
        delta_pairs=max(0, delta_pairs),
    )
    if exporter.run() != 0:
        raise SiteError(
            "no runs in the history database: record one first "
            "(testo run, testo ingest or pytest --testo)"
        )
    manifest = json.loads((exporter.data_dir / "manifest.json").read_text(encoding="utf-8"))
    return SiteResult(out_dir=out_dir, base_path=base_path, runs=len(manifest["run_ids"]))
