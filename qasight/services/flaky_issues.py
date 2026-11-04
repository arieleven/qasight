"""A GitHub issue for each test that turns flaky, closed again once it is stable.

Off unless ``TESTO_ISSUES=github``. After every recorded run
(:class:`~qasight.services.cycle_run.CycleRunService`, so ``qasight run``,
``qasight ingest`` and the server's ingest and runs) :func:`sync_after_run` does
one pass for the run's cycle:

1. **Close.** Each open issue carrying this cycle's hidden marker
   (``<!-- testo-flaky:<cycle>:<test key> -->``) whose test is no longer flaky
   and passed its last ``TESTO_ISSUES_CLOSE_AFTER`` runs gets a comment and is
   closed as completed. Issues without the marker are never touched.
2. **Open.** Each test the run made flaky
   (:func:`~qasight.services.flaky_service.newly_flaky_tests`) gets one issue,
   unless an open issue already has its marker or its title (the GitHub Action's
   older ``flaky-issues`` titles them the same way). At most
   :data:`MAX_OPEN_PER_RUN` per pass; the rest are listed and wait for the next.

``qasight issues sync`` runs the same pass by hand, for every flaky test of the
cycle. Everything here is best-effort: a GitHub error is one warning line on
stderr and never changes the run's exit code.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote, unquote

from qasight.services import flaky_service, test_history
from qasight.services.flaky_service import DEFAULT_WINDOW, FlakyTest
from qasight.services.github_issues import DEFAULT_API_URL, GitHubClient, GitHubError
from qasight.services.test_ownership import owners_for

ENABLE_ENV = "TESTO_ISSUES"
TOKEN_ENVS = ("TESTO_GITHUB_TOKEN", "GITHUB_TOKEN")
REPO_ENV = "TESTO_ISSUES_REPO"
LABELS_ENV = "TESTO_ISSUES_LABELS"
ASSIGN_ENV = "TESTO_ISSUES_ASSIGN"
CLOSE_AFTER_ENV = "TESTO_ISSUES_CLOSE_AFTER"
UI_URL_ENV = "TESTO_UI_URL"
API_URL_ENV = "GITHUB_API_URL"

DEFAULT_LABELS: tuple[str, ...] = ("flaky-test",)
DEFAULT_CLOSE_AFTER = 10
MAX_OPEN_PER_RUN = 5
MAX_CLOSE_PER_RUN = 10
_MESSAGE_LIMIT = 1500
_TITLE_LIMIT = 256
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_USER = re.compile(r"^@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))$")
_MARKER = re.compile(r"<!-- testo-flaky:([^:\s]+):(\S+) -->")
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")
_MARKS = {"passed": "✅", "failed": "❌", "broken": "❌", "skipped": "⏭"}


class IssuesClient(Protocol):
    """What the pass needs from GitHub (:class:`GitHubClient`, or a fake in tests)."""

    def list_open_issues(self, *, labels: tuple[str, ...] = ()) -> list[dict[str, Any]]: ...

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...] = (),
        assignees: tuple[str, ...] = (),
    ) -> dict[str, Any]: ...

    def comment(self, number: int, body: str) -> None: ...

    def close(self, number: int) -> None: ...


@dataclass(frozen=True)
class IssuesConfig:
    token: str = ""
    repository: str | None = None
    labels: tuple[str, ...] = DEFAULT_LABELS
    assign: bool = False
    close_after: int = DEFAULT_CLOSE_AFTER
    api_url: str = DEFAULT_API_URL
    ui_url: str | None = None


def enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(ENABLE_ENV) or "").strip().lower() == "github"


def config_from_env(env: Mapping[str, str] | None = None) -> IssuesConfig:
    """Settings from the environment; :func:`enabled` says whether the run hook is on."""
    env = os.environ if env is None else env
    token = next((env[k].strip() for k in TOKEN_ENVS if (env.get(k) or "").strip()), "")
    raw_labels = env.get(LABELS_ENV)
    labels = (
        tuple(label.strip() for label in raw_labels.split(",") if label.strip())
        if raw_labels is not None
        else DEFAULT_LABELS
    )
    try:
        close_after = max(1, int((env.get(CLOSE_AFTER_ENV) or "").strip() or DEFAULT_CLOSE_AFTER))
    except ValueError:
        close_after = DEFAULT_CLOSE_AFTER
    return IssuesConfig(
        token=token,
        repository=(env.get(REPO_ENV) or "").strip() or None,
        labels=labels,
        assign=(env.get(ASSIGN_ENV) or "").strip().lower() in {"1", "true", "yes", "on"},
        close_after=close_after,
        api_url=(env.get(API_URL_ENV) or "").strip() or DEFAULT_API_URL,
        ui_url=(env.get(UI_URL_ENV) or "").strip().rstrip("/") or None,
    )


# --- issue text ---------------------------------------------------------------------------------


def marker(cycle: str, key: str) -> str:
    """The hidden line that ties an issue to one test; percent-encoded so nothing in it ends it."""
    return f"<!-- testo-flaky:{quote(cycle, safe='')}:{quote(key, safe='')} -->"


def parse_marker(body: str | None) -> tuple[str, str] | None:
    """``(cycle, key)`` of the issue's marker, or ``None`` when it has none."""
    match = _MARKER.search(body or "")
    return (unquote(match.group(1)), unquote(match.group(2))) if match else None


def issue_title(name: str, cycle: str) -> str:
    return f"Flaky test: {name} ({cycle})"[:_TITLE_LIMIT]


def _code(text: str) -> str:
    """``text`` as an inline code span that no backtick in it can close (as in the PR comment)."""
    text = " ".join(str(text).split())
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _code_block(text: str) -> list[str]:
    """``text`` in a fenced block longer than any backtick run inside it."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence, text, fence]


def test_page_url(ui_url: str | None, cycle: str, key: str) -> str | None:
    if not ui_url:
        return None
    return f"{ui_url}/cycles/{quote(cycle, safe='')}/test?key={quote(key, safe='')}"


def issue_body(
    test: FlakyTest,
    *,
    cycle: str,
    key: str,
    message: str = "",
    owners: tuple[str, ...] = (),
    page_url: str | None = None,
    close_after: int = DEFAULT_CLOSE_AFTER,
) -> str:
    history = "".join(_MARKS.get(s, "·") for s in test.history)
    lines = [
        marker(cycle, key),
        f"{_code(test.name)} in cycle {_code(cycle)} is flaky: "
        + ("; ".join(test.reasons) if test.reasons else "it flips between passing and failing")
        + ".",
        "",
        f"- Last {test.runs} runs: {test.passed} passed, {test.failed} failed, "
        f"{test.flips} flips (flip rate {round(test.flip_rate * 100)}%)",
        f"- History, oldest to newest: {history}",
    ]
    if test.full_name and test.full_name != test.name:
        lines.append(f"- Test: {_code(test.full_name)}")
    if owners:
        lines.append(f"- Owners (CODEOWNERS): {' '.join(owners)}")
    if page_url:
        lines.append(f"- [Test history in QASight]({page_url})")
    if message:
        lines += ["", "Last failure:", "", *_code_block(message[:_MESSAGE_LIMIT])]
    if _SAFE_NAME.fullmatch(cycle):
        lines += ["", f"Check it locally with `qasight flaky --cycle {cycle}`."]
    lines += [
        "",
        "<sub>Opened by [QASight](https://github.com/arieleven/qasight). It closes "
        f"this issue when the test is no longer flaky and has passed its last {close_after} "
        "runs.</sub>",
    ]
    return "\n".join(lines)


def close_comment(*, close_after: int) -> str:
    return (
        f"This test is no longer flaky and passed its last {close_after} runs, "
        "so QASight is closing this issue. It opens a new one if the test starts "
        "flipping again."
    )


# --- the pass -----------------------------------------------------------------------------------


IssueActionKind = Literal["open", "close", "exists", "deferred"]


@dataclass(frozen=True)
class IssueAction:
    kind: IssueActionKind
    test: str
    number: int | None = None
    url: str | None = None
    note: str = ""


@dataclass
class SyncResult:
    cycle: str
    repository: str | None
    dry_run: bool
    actions: list[IssueAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def of(self, kind: IssueActionKind) -> list[IssueAction]:
        return [a for a in self.actions if a.kind == kind]


@dataclass(frozen=True)
class _Seen:
    """One test in the cycle's recent runs."""

    key: str  # full case key (FlakyTest.key is cut to 80 characters)
    statuses: tuple[str, ...]  # newest first
    message: str  # last failure message, "" if none


def _recent(cycle: str, window: int) -> dict[str, _Seen]:
    """Recent tests of ``cycle`` by ``FlakyTest.key``."""
    _, per_test = test_history._observations(cycle, window)
    seen: dict[str, _Seen] = {}
    for key, observed in per_test.items():
        statuses = tuple(str(case.get("status")) for _, case in reversed(observed))
        message = next(
            (
                str(case.get("message") or "")
                for _, case in reversed(observed)
                if str(case.get("status")) in {"failed", "broken"}
            ),
            "",
        )
        seen[key[:80]] = _Seen(key=key, statuses=statuses, message=message)
    return seen


def _assignees(owners: tuple[str, ...]) -> tuple[str, ...]:
    """GitHub logins among CODEOWNERS owners: ``@user`` only, not ``@org/team`` or emails."""
    return tuple(m.group(1) for o in owners if (m := _GITHUB_USER.match(o)))


def _create(client: IssuesClient, *, assignees: tuple[str, ...], **kwargs: Any) -> dict[str, Any]:
    try:
        return client.create_issue(assignees=assignees, **kwargs)
    except GitHubError as exc:
        # 422: an owner can't be assigned (not a collaborator, no such user). Open it anyway.
        if assignees and exc.status == 422:
            return client.create_issue(**kwargs)
        raise


def resolve_repository(cycle: str, config: IssuesConfig) -> tuple[Path | None, str | None]:
    """The cycle's checkout (for CODEOWNERS) and ``owner/name``: ``TESTO_ISSUES_REPO`` wins."""
    checkout, recorded = test_history.cycle_source(cycle)
    repository = config.repository or recorded
    if repository and not _GITHUB_REPO.match(repository):
        repository = None
    return checkout, repository


def sync_cycle(
    *,
    cycle: str,
    client: IssuesClient,
    config: IssuesConfig,
    repository: str | None,
    checkout: Path | None = None,
    run_id: str | None = None,
    only_new: bool = True,
    dry_run: bool = False,
    window: int = DEFAULT_WINDOW,
) -> SyncResult:
    """One close-then-open pass for ``cycle``. GitHub errors end the pass as a warning."""
    result = SyncResult(cycle=cycle, repository=repository, dry_run=dry_run)
    report = flaky_service.find_flaky_tests(cycle=cycle, window=window)
    flaky_now = {t.key: t for t in report.flaky}
    if only_new:
        candidates: tuple[FlakyTest, ...] = flaky_service.newly_flaky_tests(
            cycle=cycle, run_id=run_id, window=window
        )
    else:
        candidates = report.flaky
    recent = _recent(cycle, max(window, config.close_after))

    try:
        existing = client.list_open_issues(labels=config.labels)
    except GitHubError as exc:
        result.warnings.append(f"could not list open issues: {exc}")
        return result

    by_marker: dict[str, dict[str, Any]] = {}
    titles: dict[str, dict[str, Any]] = {}
    for issue in existing:
        titles.setdefault(str(issue.get("title") or ""), issue)
        found = parse_marker(issue.get("body"))
        if found is not None and found[0] == cycle:
            by_marker.setdefault(found[1], issue)

    try:
        _close_pass(result, client, config, by_marker, flaky_now, recent, dry_run)
        _open_pass(
            result,
            client,
            config,
            candidates,
            by_marker,
            titles,
            recent,
            checkout,
            dry_run,
        )
    except GitHubError as exc:
        result.warnings.append(str(exc))
    return result


def _close_pass(
    result: SyncResult,
    client: IssuesClient,
    config: IssuesConfig,
    by_marker: dict[str, dict[str, Any]],
    flaky_now: dict[str, FlakyTest],
    recent: dict[str, _Seen],
    dry_run: bool,
) -> None:
    closed = 0
    for key, issue in by_marker.items():
        seen = recent.get(key[:80])
        if key[:80] in flaky_now or seen is None:
            continue
        last = seen.statuses[: config.close_after]
        if len(last) < config.close_after or any(s != "passed" for s in last):
            continue
        number = int(issue.get("number") or 0)
        title = str(issue.get("title") or key)
        if closed >= MAX_CLOSE_PER_RUN:
            result.actions.append(IssueAction("deferred", title, number, note="close limit"))
            continue
        if not dry_run:
            client.comment(number, close_comment(close_after=config.close_after))
            client.close(number)
        closed += 1
        result.actions.append(IssueAction("close", title, number, issue.get("html_url")))


def _open_pass(
    result: SyncResult,
    client: IssuesClient,
    config: IssuesConfig,
    candidates: tuple[FlakyTest, ...],
    by_marker: dict[str, dict[str, Any]],
    titles: dict[str, dict[str, Any]],
    recent: dict[str, _Seen],
    checkout: Path | None,
    dry_run: bool,
) -> None:
    cycle = result.cycle
    opened = 0
    for test in candidates:
        seen = recent.get(test.key)
        key = seen.key if seen else test.key
        title = issue_title(test.name, cycle)
        open_one = by_marker.get(key) or by_marker.get(test.key) or titles.get(title)
        if open_one is not None:
            result.actions.append(
                IssueAction(
                    "exists", title, int(open_one.get("number") or 0), open_one.get("html_url")
                )
            )
            continue
        if opened >= MAX_OPEN_PER_RUN:
            result.actions.append(
                IssueAction("deferred", title, note=f"limit {MAX_OPEN_PER_RUN} per run")
            )
            continue
        owners = owners_for(checkout, test.full_name, test.group)
        body = issue_body(
            test,
            cycle=cycle,
            key=key,
            message=seen.message if seen else "",
            owners=owners,
            page_url=test_page_url(config.ui_url, cycle, key),
            close_after=config.close_after,
        )
        assignees = _assignees(owners) if config.assign else ()
        opened += 1
        if dry_run:
            note = f"assign {', '.join(assignees)}" if assignees else ""
            result.actions.append(IssueAction("open", title, note=note))
            continue
        made = _create(client, title=title, body=body, labels=config.labels, assignees=assignees)
        number = int(made.get("number") or 0)
        by_marker[key] = made
        titles[title] = made
        result.actions.append(IssueAction("open", title, number, made.get("html_url")))


# --- after a run ---------------------------------------------------------------------------------


def _warn(text: str) -> None:
    sys.stderr.write(f"qasight issues: {text}\n")


def sync_after_run(
    *,
    cycle: str,
    run_id: str | None,
    env: Mapping[str, str] | None = None,
    client: IssuesClient | None = None,
) -> SyncResult | None:
    """The run hook: does nothing unless ``TESTO_ISSUES=github``; never raises."""
    if not enabled(env) or not cycle or not run_id:
        return None
    try:
        config = config_from_env(env)
        checkout, repository = resolve_repository(cycle, config)
        if client is None:
            if not config.token:
                _warn(f"{ENABLE_ENV}=github needs {' or '.join(TOKEN_ENVS)}; skipped.")
                return None
            if repository is None:
                _warn(f"no GitHub repository known for {cycle}; set {REPO_ENV}=owner/name.")
                return None
            client = GitHubClient(token=config.token, repository=repository, api_url=config.api_url)
        result = sync_cycle(
            cycle=cycle,
            client=client,
            config=config,
            repository=repository,
            checkout=checkout,
            run_id=run_id,
            only_new=True,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort: never fail or stop the run
        _warn(f"skipped ({type(exc).__name__}: {exc})")
        return None
    for line in describe(result):
        _warn(line)
    return result


def describe(result: SyncResult) -> list[str]:
    """One line per thing the pass did (or would do, in a dry run), then the warnings."""
    verbs = {
        "open": "would open" if result.dry_run else "opened",
        "close": "would close" if result.dry_run else "closed",
        "exists": "already open",
        "deferred": "left for the next run",
    }
    lines: list[str] = []
    for action in result.actions:
        ref = f" #{action.number}" if action.number else ""
        extra = f" ({action.note})" if action.note else ""
        lines.append(f"{verbs.get(action.kind, action.kind)}{ref}: {action.test}{extra}")
    lines += [f"warning: {w}" for w in result.warnings]
    return lines
