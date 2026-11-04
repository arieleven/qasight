"""AI review of the project under test, one review "skill" at a time.

The deterministic score (:mod:`~qasight.services.project_health`) says how
the tests are doing. This adds an opinion on top: the configured AI provider
reads the same history (test names, failure messages, flaky and slow tests,
the pyramid, run results) and each enabled skill returns a 0-100 score, a
summary and findings. It never reads source code.

Skills are short built-in rubrics about the tests only: why tests flake, which
failures share a cause, strategy gaps, test debt, slow tests, churn and naming.
They don't judge the product, its architecture or its UI (code review tools do
that). Users switch each one on or off in the UI; :data:`SKILLS` is the catalog.

The score is an AI estimate: it costs one provider call, and asking again can
give a different answer. A review is stored on the newest run it looked at
(``ai_project_review_v1`` in the run's metadata, keyed by scope), so it
survives a restart and works on every database backend.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from qasight.history.maintenance import upsert_run_metadata
from qasight.history.read_model import get_run_metadata, list_cycle_runs, list_recent_runs
from qasight.security.redaction import redact_error_message, redact_text
from qasight.services.ai import (
    AiGenerationRequest,
    AiProviderError,
    ProviderMisconfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    UnsupportedProviderModelError,
    build_ai_provider,
)
from qasight.services.ai.integration_settings import InMemoryAiSettingsStore
from qasight.services.project_health import ProjectHealth, grade_for, project_health
from qasight.services.run_failures import load_run_failures
from qasight.services.test_history import list_cycle_tests, test_presence

METADATA_KEY = "ai_project_review_v1"
_ALL = "*"
# A review answers in JSON for every enabled skill, which needs more room than
# the 300-token default of a run summary, and more time on a free provider.
_TOKENS_PER_SKILL = 450
_MAX_OUTPUT_TOKENS = 4000
_MIN_TIMEOUT_S = 60.0
# Runs searched (newest first) for a stored review of the scope.
_STORED_SCAN = 50
_MAX_TEST_NAMES = 150
_MAX_FAILURES = 15
_MAX_MESSAGE = 300

Severity = Literal["high", "medium", "low"]
_SEVERITIES: dict[str, Severity] = {"high": "high", "medium": "medium", "low": "low"}


@dataclass(frozen=True)
class ReviewSkill:
    id: str
    name: str
    # What part of test health it looks at, shown as a tag.
    category: str
    # One line for the collapsed row.
    tagline: str
    # A short paragraph that sums up the rubric, shown when the row is expanded.
    summary: str
    checks: tuple[str, ...]
    reads: tuple[str, ...]
    default_on: bool
    # Sent to the model, after the shared instructions.
    rubric: str


SKILLS: tuple[ReviewSkill, ...] = (
    ReviewSkill(
        id="reliability",
        name="Why tests are flaky",
        category="Reliability",
        tagline="Which tests fail for no reason, and why.",
        summary=(
            "Looks at the tests that pass and fail on the same code and guesses why: timing, "
            "shared state, test order, a slow service. Flaky tests teach people to ignore "
            "red builds, which is how real bugs get through."
        ),
        checks=(
            "Flaky tests and how often they flip",
            "Likely cause per flaky test (timeouts, ordering, shared data, network)",
            "Tests waived by quarantine rules and whether they are piling up",
        ),
        reads=("Flaky tests", "Run results per cycle", "Quarantined tests"),
        default_on=True,
        rubric=(
            "For each flaky test give the most likely cause from its name, group and "
            "failure messages, and a fix. Penalise high flip rates and growing quarantine "
            "lists. 90+ means a red build almost always means a real bug."
        ),
    ),
    ReviewSkill(
        id="failures",
        name="Related failures",
        category="Failures",
        tagline="Which failures share one cause.",
        summary=(
            "Reads the failure messages of the latest runs and groups them into likely "
            "causes: a real bug, a broken environment, a timeout, a bad assertion. It names "
            "the one to fix first."
        ),
        checks=(
            "Failures that share one root cause",
            "Errors that point at code (exceptions, wrong values) vs. setup (connection refused, missing files)",
            "Failure messages too vague to act on",
        ),
        reads=("Failing tests and their first message line", "Which cycle and stage they ran in"),
        default_on=True,
        rubric=(
            "Group the failing tests by likely root cause and say which cause to fix first. "
            "Score the state of the failures: 90+ means none or only trivial ones, below 50 "
            "means real defects across several areas. Do not invent causes the messages do "
            "not show."
        ),
    ),
    ReviewSkill(
        id="strategy",
        name="Test strategy gaps",
        category="Strategy",
        tagline="Are the right kinds of tests in place?",
        summary=(
            "Checks the balance of unit, integration and end-to-end tests and which areas "
            "of the project the test names cover. Suggests the tests that would add the most "
            "confidence next."
        ),
        checks=(
            "Shape of the test pyramid",
            "Areas with many tests vs. areas with few or none",
            "Missing kinds of tests (error paths, contracts, end-to-end smoke)",
        ),
        reads=("Test pyramid counts", "Test names and groups", "Frameworks per stage"),
        default_on=True,
        rubric=(
            "Judge the test mix and coverage of areas as seen from the test names and the "
            "pyramid. Suggest up to three specific tests or suites to add. 90+ means a clear "
            "pyramid and every visible area tested."
        ),
    ),
    ReviewSkill(
        id="debt",
        name="Test debt",
        category="Maintenance",
        tagline="What will slow the team down later.",
        summary=(
            "Finds the test suite's maintenance backlog: skipped tests, quarantined tests, "
            "duplicated or oddly named tests and tests that keep getting slower. Ranks what "
            "to pay down first."
        ),
        checks=(
            "Skipped and quarantined tests that nobody fixes",
            "Duplicate or near-duplicate tests",
            "Tests that got slower",
        ),
        reads=("Skipped and quarantined counts", "Test names", "Tests that got slower"),
        default_on=True,
        rubric=(
            "List the test debt in order of payoff, cheapest big win first. 90+ means "
            "almost no skipped, quarantined or duplicated tests."
        ),
    ),
    ReviewSkill(
        id="speed",
        name="Slow tests",
        category="Speed",
        tagline="How long people wait for an answer.",
        summary=(
            "Looks at how long runs take, which tests take the most time and which are "
            "getting slower. Fast feedback is what makes people run tests before pushing."
        ),
        checks=(
            "Run duration per cycle",
            "The slowest tests and their share of the run",
            "Tests that got slower recently",
        ),
        reads=("Run durations", "Slowest tests (median time)", "Tests that got slower"),
        default_on=True,
        rubric=(
            "Judge feedback time. Name the slowest tests worth splitting, mocking or running "
            "in parallel. 90+ means the main cycle finishes in a few minutes with no single "
            "test dominating."
        ),
    ),
    ReviewSkill(
        id="churn",
        name="Test churn",
        category="Coverage",
        tagline="Which tests appeared or disappeared lately?",
        summary=(
            "Compares the tests in recent runs with older ones. Tests that vanish without a "
            "replacement, groups that shrink and new code areas that got no new tests are "
            "where coverage quietly drops."
        ),
        checks=(
            "Tests removed in recent runs with nothing replacing them",
            "Groups whose test count shrinks",
            "New groups with only one or two tests",
        ),
        reads=("Test names per run", "When each test was first and last seen"),
        default_on=True,
        rubric=(
            "Use first_seen / last_seen to spot tests that disappeared or groups that got "
            "few tests. Say plainly when the history is too short to tell. 90+ means the "
            "suite only grows or tests were clearly replaced."
        ),
    ),
    ReviewSkill(
        id="naming",
        name="Test names and messages",
        category="Readability",
        tagline="Can a newcomer tell what broke?",
        summary=(
            "Reads test names and failure messages the way a new teammate would. Good "
            "names say the behaviour and the expected result, and good messages say what "
            "was expected and what happened."
        ),
        checks=(
            "Names that describe behaviour vs. test1, test_it_works",
            "Failure messages with expected and actual values",
            "Consistent naming across groups",
        ),
        reads=("Test names", "Failure messages"),
        default_on=False,
        rubric=(
            "Quote two or three weak names or messages and suggest better ones. 90+ means "
            "names read like specifications and failures explain themselves."
        ),
    ),
)

SKILLS_BY_ID = {s.id: s for s in SKILLS}


@dataclass(frozen=True)
class ReviewFinding:
    severity: Severity
    title: str
    detail: str


@dataclass(frozen=True)
class SkillResult:
    skill_id: str
    name: str
    score: int | None
    summary: str
    findings: tuple[ReviewFinding, ...] = ()


@dataclass(frozen=True)
class ProjectReview:
    scope: str | None
    status: Literal["available", "not_generated", "failed"]
    score: int | None = None
    grade: str | None = None
    summary: str | None = None
    skills: tuple[SkillResult, ...] = ()
    provider: str | None = None
    model: str | None = None
    generated_at: float | None = None
    # The newest run the review looked at; ``stale`` when newer runs exist since.
    based_on_run_id: str | None = None
    stale: bool = False
    error_code: str | None = None
    error_message: str | None = None
    # What was sent, so the page can say it.
    context_stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_INSTRUCTIONS = """\
You review the automated tests of a software project for its engineering team.
You only see test results and test names collected by a test orchestrator, never source code.
Judge the tests only: not the product, its features, its architecture or its UI.
Use only the evidence below. Do not invent tests, files or causes. When evidence is thin, say so.

Reply with one JSON object and nothing else (no Markdown fences):
{"summary": "two sentences on the overall state",
 "skills": [{"id": "<skill id>", "score": <0-100 or null>, "summary": "one or two sentences",
             "findings": [{"severity": "high|medium|low", "title": "short", "detail": "one sentence, concrete"}]}]}
Give every skill listed below exactly one entry, in the same order, with at most 4 findings each.
Scores: 90-100 excellent, 70-89 good, 50-69 needs work, below 50 poor. Be strict and consistent.

Skills:
"""


def _scope_runs(cycle: str | None) -> list[Any]:
    return list_cycle_runs(cycle) if cycle is not None else list_recent_runs(limit=500)


def history_evidence(health: ProjectHealth, *, max_chars: int) -> tuple[str, dict[str, int]]:
    """The evidence block: scores, per-cycle results, failures, flaky/slow tests, test names."""
    cycles: list[dict[str, Any]] = []
    failures = names = 0
    for row in health.by_cycle:
        tests = list_cycle_tests(row.cycle).tests
        failing = load_run_failures(row.run_id)
        failing_tests = [
            {
                "name": t.name,
                "group": t.group,
                "stage": t.stage,
                "new": t.new,
                "failing_runs": t.failing_runs,
                "message": redact_text(t.message)[:_MAX_MESSAGE],
            }
            for t in (failing.tests if failing else ())[:_MAX_FAILURES]
        ]
        failures += len(failing_tests)
        slowest = sorted(
            (t for t in tests if t.median_duration_ms), key=lambda t: -(t.median_duration_ms or 0)
        )[:10]
        listed = [f"{t.group} :: {t.name}" for t in tests[:_MAX_TEST_NAMES]]
        names += len(listed)
        presence = test_presence(row.cycle)
        churn = {
            "runs_analysed": 1 + max((p[1] for p in presence.values()), default=0),
            "gone_from_latest_run": [
                {"name": n, "last_seen_runs_ago": last}
                for n, _, last in presence.values()
                if last > 0
            ][:20],
            "new_in_last_3_runs": [n for n, first, _ in presence.values() if first < 3][:20]
            if any(first >= 3 for _, first, _ in presence.values())
            else [],
        }
        cycles.append(
            {
                "cycle": row.cycle,
                "latest_run_passed": row.passed_latest,
                "tests": row.tests,
                "failing": row.failing,
                "green_runs": f"{row.green_runs} of {row.runs}",
                "flaky": [
                    {"name": t.name, "failed": t.failed, "runs": t.runs} for t in tests if t.flaky
                ][:10],
                "got_slower": [
                    {"name": t.name, "slowdown_ms": t.slowdown_ms} for t in tests if t.slowdown_ms
                ][:10],
                "slowest": [{"name": t.name, "median_ms": t.median_duration_ms} for t in slowest],
                "failing_tests": failing_tests,
                "test_names": listed,
                "churn": churn,
            }
        )
    evidence = {
        "deterministic_score": health.score,
        "signals": [{"name": s.name, "score": s.score, "value": s.value} for s in health.signals],
        "quarantined_and_skipped": [
            f.title for f in health.findings if f.kind in ("quarantined", "skipped")
        ],
        "cycles": cycles,
    }
    text = json.dumps(evidence, default=str)
    truncated = 0
    # Test names are the bulkiest and least important part: thin them out first.
    while len(text) > max_chars and any(c["test_names"] for c in cycles):
        truncated = 1
        for c in cycles:
            c["test_names"] = c["test_names"][: len(c["test_names"]) // 2]
        text = json.dumps(evidence, default=str)
    names = sum(len(c["test_names"]) for c in cycles)
    if len(text) > max_chars:
        truncated = 1
        text = text[: max(0, max_chars - 40)] + "\n...[evidence truncated]"
    return text, {
        "cycles": len(cycles),
        "failing_tests": failures,
        "test_names": names,
        "chars": len(text),
        "truncated": truncated,
    }


def build_prompt(
    health: ProjectHealth, skills: list[ReviewSkill], *, max_chars: int
) -> tuple[str, dict[str, int]]:
    rubric = "\n".join(f'- id "{s.id}" ({s.name}): {s.rubric}' for s in skills)
    evidence, stats = history_evidence(health, max_chars=max_chars)
    return f"{_INSTRUCTIONS}{rubric}\n\nEvidence (JSON):\n{evidence}\n", stats


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _score(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return max(0, min(100, round(value)))


def parse_reply(text: str, skills: list[ReviewSkill]) -> tuple[str, tuple[SkillResult, ...]]:
    """The summary and one result per requested skill; raises ``ValueError`` on bad JSON."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("reply is not a JSON object")
    by_id = {
        str(item.get("id")): item for item in payload.get("skills") or () if isinstance(item, dict)
    }
    results = []
    for skill in skills:
        item = by_id.get(skill.id, {})
        findings = tuple(
            ReviewFinding(
                severity=_SEVERITIES.get(str(f.get("severity")), "medium"),
                title=str(f.get("title") or "")[:200],
                detail=str(f.get("detail") or "")[:600],
            )
            for f in (item.get("findings") or ())[:4]
            if isinstance(f, dict) and f.get("title")
        )
        results.append(
            SkillResult(
                skill_id=skill.id,
                name=skill.name,
                score=_score(item.get("score")),
                summary=str(item.get("summary") or "No answer for this skill.")[:800],
                findings=findings,
            )
        )
    return str(payload.get("summary") or "")[:800], tuple(results)


def _review_from_dict(payload: dict[str, Any]) -> ProjectReview:
    skills = tuple(
        SkillResult(
            skill_id=str(s.get("skill_id")),
            name=str(s.get("name")),
            score=s.get("score"),
            summary=str(s.get("summary") or ""),
            findings=tuple(ReviewFinding(**f) for f in s.get("findings") or ()),
        )
        for s in payload.get("skills") or ()
    )
    return ProjectReview(
        scope=payload.get("scope"),
        status=payload.get("status", "available"),
        score=payload.get("score"),
        grade=payload.get("grade"),
        summary=payload.get("summary"),
        skills=skills,
        provider=payload.get("provider"),
        model=payload.get("model"),
        generated_at=payload.get("generated_at"),
        based_on_run_id=payload.get("based_on_run_id"),
        context_stats=dict(payload.get("context_stats") or {}),
    )


class ProjectReviewService:
    def __init__(self, *, settings_store: InMemoryAiSettingsStore) -> None:
        self._settings_store = settings_store

    def get_review(self, *, cycle: str | None = None) -> ProjectReview:
        """The newest stored review of the scope, ``stale`` when runs came in after it."""
        key = cycle or _ALL
        runs = _scope_runs(cycle)
        for index, run in enumerate(runs[:_STORED_SCAN]):
            stored = (get_run_metadata(run_id=run.run_id) or {}).get(METADATA_KEY) or {}
            if isinstance(stored, dict) and isinstance(stored.get(key), dict):
                return replace(_review_from_dict(stored[key]), stale=index > 0)
        return ProjectReview(scope=cycle, status="not_generated")

    def generate(self, *, cycle: str | None, skill_ids: list[str]) -> ProjectReview:
        skills = [SKILLS_BY_ID[i] for i in dict.fromkeys(skill_ids) if i in SKILLS_BY_ID]
        if not skills:
            return self._failed(cycle, "no_skills", "Switch on at least one skill.")
        settings = self._settings_store.get()
        if not settings.enabled:
            return self._failed(
                cycle, "ai_feature_disabled", "AI is off. Switch it on in AI settings."
            )
        health = project_health(cycle=cycle)
        runs = _scope_runs(cycle)
        if not runs or health.cycles == 0:
            return self._failed(cycle, "no_runs", "There are no recorded runs to review yet.")

        cfg = settings.to_provider_config()
        cfg = replace(
            cfg,
            timeout_s=max(cfg.timeout_s, _MIN_TIMEOUT_S),
            max_output_tokens=min(
                _MAX_OUTPUT_TOKENS,
                max(cfg.max_output_tokens, 300 + _TOKENS_PER_SKILL * len(skills)),
            ),
        )
        prompt, stats = build_prompt(health, skills, max_chars=cfg.max_input_chars)
        try:
            provider = build_ai_provider(config=cfg, runtime_api_key=settings.runtime_api_key)
            result = provider.generate(
                AiGenerationRequest(
                    prompt=prompt, max_output_tokens=cfg.max_output_tokens, temperature=0.1
                )
            )
        except ProviderMisconfiguredError as exc:
            return self._failed(cycle, "provider_misconfigured", redact_error_message(exc))
        except ProviderTimeoutError:
            return self._failed(cycle, "provider_timeout", "The AI provider took too long.")
        except ProviderRateLimitError:
            return self._failed(
                cycle, "provider_rate_limited", "The AI provider is rate limiting. Try again soon."
            )
        except UnsupportedProviderModelError as exc:
            return self._failed(cycle, "unsupported_provider_model", redact_error_message(exc))
        except AiProviderError as exc:
            return self._failed(cycle, "provider_error", redact_error_message(exc))

        try:
            summary, results = parse_reply(result.text, skills)
        except (ValueError, json.JSONDecodeError):
            return self._failed(
                cycle,
                "unparseable_reply",
                "The model did not answer in the expected format. Try again or pick another model.",
            )
        scored = [r.score for r in results if r.score is not None]
        score = round(sum(scored) / len(scored)) if scored else None
        review = ProjectReview(
            scope=cycle,
            status="available",
            score=score,
            grade=grade_for(score),
            summary=summary,
            skills=results,
            provider=result.provider,
            model=result.model,
            generated_at=time.time(),
            based_on_run_id=runs[0].run_id,
            context_stats=stats,
        )
        stored = (get_run_metadata(run_id=runs[0].run_id) or {}).get(METADATA_KEY) or {}
        stored = dict(stored) if isinstance(stored, dict) else {}
        stored[cycle or _ALL] = review.to_dict()
        upsert_run_metadata(run_id=runs[0].run_id, metadata_patch={METADATA_KEY: stored})
        return review

    @staticmethod
    def _failed(cycle: str | None, code: str, message: str) -> ProjectReview:
        return ProjectReview(scope=cycle, status="failed", error_code=code, error_message=message)
