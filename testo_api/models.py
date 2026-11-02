from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base for every request/response body in the HTTP contract.

    The response always contains fields that have a default, so the OpenAPI
    schema marks them required in responses. That keeps the generated
    frontend types (``frontend/src/lib/api-schema.ts``) from turning every
    defaulted field into an optional one. Request schemas are unaffected.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class StageSummary(ApiModel):
    name: str
    equipment: str
    target_repo: str
    args: list[str] = Field(default_factory=list)
    timeout_s: float | None = None
    workers: int | None = None


class CycleLatestRun(ApiModel):
    """The newest recorded run of a cycle: enough for a list card to show its result."""

    run_id: str
    created_at: float
    status: str | None = None
    returncode: int
    health_pct: float | None = None
    total_tests: int | None = None
    passed: int | None = None
    failed: int | None = None
    branch: str | None = None
    commit: str | None = None


class CycleSummary(ApiModel):
    name: str
    description: str | None = None
    stage_count: int
    equipment: list[str] = Field(default_factory=list)
    # "config": defined in testosterone.yaml, so it can be run from here.
    # "history": only recorded (``testo ingest``, CI, another machine); read-only.
    source: Literal["config", "history"] = "config"
    # None until the cycle has a recorded run.
    latest_run: CycleLatestRun | None = None


class CycleListResponse(ApiModel):
    items: list[CycleSummary]
    config_path: str | None = None


class CycleTriggerSummary(ApiModel):
    paths: list[str] = Field(default_factory=list)
    since_ref: str | None = None


class CycleDetailResponse(ApiModel):
    name: str
    description: str | None = None
    stages: list[StageSummary]
    trigger: CycleTriggerSummary | None = None
    source: Literal["config", "history"] = "config"


class CycleExecutionRequest(ApiModel):
    config_path: str | None = None
    artifacts_root: str | None = None
    stream: bool = False
    persist: bool = True
    fail_fast: bool = False
    force: bool = False
    workers_override: int | None = None
    report_db: bool = True
    async_report_db: bool = False
    reporter_override: list[str] | None = None


class AdhocExecutionRequest(ApiModel):
    """Run one framework directly, without a cycle in ``testosterone.yaml``."""

    framework: Literal["pytest", "behave", "behavex", "command", "playwright"]
    target_repo: str
    args: list[str] = Field(default_factory=list)
    timeout_s: float | None = None
    extra_env: dict[str, str] | None = None
    config_path: str | None = None
    artifacts_root: str | None = None
    persist: bool = True
    report_db: bool = True


class CycleExecutionAcceptedResponse(ApiModel):
    execution_id: str
    status: Literal["queued", "running"]
    events_url: str
    summary_url: str


class CycleExecutionStatusResponse(ApiModel):
    execution_id: str
    cycle: str
    status: Literal["queued", "running", "completed", "failed"]
    artifacts_root: str | None = None
    events_path: str | None = None
    plan_result_path: str | None = None
    error: str | None = None


class ErrorPayload(ApiModel):
    code: Literal[
        "invalid_input",
        "not_found",
        "domain_failure",
        "infra_failure",
        "internal_error",
        "provider_misconfigured",
        "provider_timeout",
        "provider_rate_limited",
        "unsupported_provider_model",
        "ai_feature_disabled",
        "summary_not_available",
    ]
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(ApiModel):
    error: ErrorPayload
    request_id: str


class RunListItem(ApiModel):
    run_id: str
    created_at: float
    returncode: int
    status: str | None = None
    cycle: str | None = None
    health_pct: float | None = None
    total_tests: int | None = None
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    broken: int | None = None
    links_under_static: dict[str, str] = Field(default_factory=dict)
    branch: str | None = None
    commit: str | None = None
    # "browser=firefox, os=linux" (testo run --env, TESTO_ENV, Allure environment.properties).
    environment: str | None = None


class RunListResponse(ApiModel):
    items: list[RunListItem]
    next_cursor: str | None = None


class StageHealth(ApiModel):
    name: str
    framework: str | None = None
    total_tests: int | None = None
    passed: int | None = None
    failed: int | None = None
    broken: int | None = None
    skipped: int | None = None
    health_pct: float | None = None
    # A failed stage's stack overflow or crash: "stack_overflow" | "crash", and the line that showed it.
    crash_kind: str | None = None
    crash_detail: str | None = None


class RunDetail(ApiModel):
    run_id: str
    status: str | None = None
    created_at: float
    started_at: float
    finished_at: float
    test_kind: str
    cycle: str | None = None
    returncode: int
    wall_duration_ms: float
    metrics_duration_ms: int | None = None
    total_tests: int | None = None
    passed: int | None = None
    failed: int | None = None
    broken: int | None = None
    skipped: int | None = None
    avg_case_ms: float | None = None
    health_pct: float | None = None
    target_repo: str | None = None
    snapshot_dir: str | None = None
    audit_json: str | None = None
    stage_health: list[StageHealth] = Field(default_factory=list)
    # What the run tested, when recorded (CI provenance or the local checkout).
    branch: str | None = None
    commit: str | None = None
    base_branch: str | None = None
    repository: str | None = None
    # "browser=firefox, os=linux" (testo run --env, TESTO_ENV, Allure environment.properties).
    environment: str | None = None
    # Line coverage stored with ``--coverage``.
    coverage_pct: float | None = None
    coverage_covered: int | None = None
    coverage_total: int | None = None


class RunDetailResponse(ApiModel):
    run: RunDetail
    metrics: dict[str, Any] | None = None
    sync: dict[str, Any] | None = None


class RunReportsResponse(ApiModel):
    allure_server_url: str | None = None
    static_links: dict[str, str] = Field(default_factory=dict)
    artifact_links: list[str] = Field(default_factory=list)
    # URL prefix (relative to the API root) the artifact_links are served under, or
    # None when the run's files aren't served (snapshot outside static/history/).
    artifacts_url: str | None = None


class RunPyramidResponse(ApiModel):
    unit: int
    integration: int
    e2e: int
    shape: Literal["healthy", "top_heavy", "mid_bulge", "irregular"]
    message: str


class RunMarkdownResponse(ApiModel):
    """A run as GitHub-flavored Markdown, the same text as ``testo report markdown``."""

    run_id: str
    markdown: str


class TriageItem(ApiModel):
    cycle: str
    # The failure message with numbers, ids and quoted values blanked: what later failures match on.
    signature: str
    cause: Literal["product_bug", "test_bug", "environment", "flaky", "other"]
    note: str = ""
    # The failure it was marked on.
    example_message: str = ""
    run_id: str = ""
    test_key: str = ""
    marked_by: str | None = None
    marked_at: float


class TriageRequest(ApiModel):
    cycle: str = Field(min_length=1, max_length=512)
    message: str = Field(min_length=1, max_length=4000)
    cause: Literal["product_bug", "test_bug", "environment", "flaky", "other"]
    note: str = Field(default="", max_length=500)
    run_id: str = Field(default="", max_length=64)
    test_key: str = Field(default="", max_length=200)


class TriageListResponse(ApiModel):
    causes: list[TriageItem]


class QuarantineItem(ApiModel):
    """A test (or glob) whose failures don't fail its cycle's runs, kept on the server."""

    cycle: str
    test: str
    # An fnmatch glob: matches several tests and is never released on its own.
    is_glob: bool
    reason: str = ""
    owner: str = ""
    added_by: str | None = None
    added_at: float


class QuarantineRequest(ApiModel):
    cycle: str = Field(min_length=1, max_length=512)
    # The test's name or full name as runs record it, or an fnmatch glob.
    test: str = Field(min_length=1, max_length=1024)
    reason: str | None = Field(default=None, max_length=500)
    owner: str | None = Field(default=None, max_length=200)


class QuarantineListResponse(ApiModel):
    items: list[QuarantineItem]
    # Clean runs in a row that release an exact-name entry (TESTO_QUARANTINE_RELEASE_AFTER); 0 is off.
    release_after: int


class TestLabelItem(ApiModel):
    """What a test is about: ``feature=Checkout``, ``severity=critical``, ``tag=smoke``..."""

    name: str
    value: str


class TestLinkItem(ApiModel):
    """A link the test's results carry: an issue or story it covers, or a doc."""

    name: str
    url: str
    # "issue" for issue and story links, "tms" for test management ids, else "link".
    type: str = "link"


class FailingTestItem(ApiModel):
    key: str
    name: str
    full_name: str
    group: str
    stage: str
    status: str
    message: str
    duration_ms: int | None = None
    failing_runs: int
    failing_since_run_id: str
    flaky: bool
    # Broke in this run: passing (or absent) on the baseline run, or, with none, in the run before.
    new: bool
    # The cause marked for this message in this cycle, on this failure or an earlier one like it.
    triage: TriageItem | None = None
    # "stack_overflow" | "crash" when the failure's message or trace shows one.
    crash_kind: str | None = None
    labels: list[TestLabelItem] = Field(default_factory=list)
    links: list[TestLinkItem] = Field(default_factory=list)


class FailureGroupItem(ApiModel):
    message: str
    count: int
    keys: list[str]


class RunFailuresResponse(ApiModel):
    run_id: str
    runs_considered: int
    new_failures: int
    # The run "new" is judged against: the latest run on the PR's target branch,
    # else the run before on the same branch. ``baseline_branch`` is set only in
    # the first case (the target branch's name).
    baseline_run_id: str | None = None
    baseline_branch: str | None = None
    # Commands that rerun just these tests (pytest node ids, Playwright --grep, Behave --name).
    rerun_commands: list[str] = Field(default_factory=list)
    tests: list[FailingTestItem]
    groups: list[FailureGroupItem]


class DeltaMetricNode(ApiModel):
    current_value: float | None = None
    baseline_value: float | None = None
    absolute_delta: float | None = None
    relative_delta_pct: float | None = None
    classification: Literal["regression", "improvement", "neutral", "unknown"]
    reason: str | None = None
    direction: Literal["higher_is_better", "lower_is_better"]
    unit: Literal["tests", "pct", "ms"]


class DeltaReliabilityMetrics(ApiModel):
    total_tests: DeltaMetricNode
    passed: DeltaMetricNode
    failed: DeltaMetricNode
    broken: DeltaMetricNode
    skipped: DeltaMetricNode
    health_pct: DeltaMetricNode


class DeltaPerformanceMetrics(ApiModel):
    wall_duration_ms: DeltaMetricNode
    metrics_duration_ms: DeltaMetricNode
    avg_case_ms: DeltaMetricNode


class DeltaMetricsResponse(ApiModel):
    reliability: DeltaReliabilityMetrics
    performance: DeltaPerformanceMetrics


class DeltaComparisonMeta(ApiModel):
    current_run_id: str
    baseline_run_id: str
    current_test_kind: str
    baseline_test_kind: str


class DeltaStatusSummaryResponse(ApiModel):
    regressions: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


class DeltaStageDelta(ApiModel):
    stage_name: str
    framework: str | None = None
    baseline_total_tests: int | None = None
    current_total_tests: int | None = None
    baseline_passed: int | None = None
    current_passed: int | None = None
    baseline_health_pct: float | None = None
    current_health_pct: float | None = None
    health_pct_delta: float | None = None
    classification: Literal["regression", "improvement", "neutral", "unknown"]


class DeltaComparisonResponse(ApiModel):
    comparison: DeltaComparisonMeta
    metrics: DeltaMetricsResponse
    status_summary: DeltaStatusSummaryResponse
    highlights: list[str] = Field(default_factory=list)
    stage_deltas: list[DeltaStageDelta] = Field(default_factory=list)


class DeltaCaseChange(ApiModel):
    key: str
    name: str
    group: str
    baseline_status: str | None = None
    current_status: str | None = None
    kind: Literal["added", "removed", "regression", "fix", "status_change"]
    duration_delta_ms: int | None = None


class DeltaCaseChangesResponse(ApiModel):
    current_run_id: str
    baseline_run_id: str
    changes: list[DeltaCaseChange] = Field(default_factory=list)


class FlakyTestItem(ApiModel):
    key: str
    name: str
    group: str
    runs: int
    passed: int
    failed: int
    flips: int
    flip_rate: float
    last_status: str
    history: list[str] = Field(default_factory=list)
    # CODEOWNERS owners of the test's file, and GitHub's new-issue form prefilled for it.
    owners: list[str] = Field(default_factory=list)
    issue_url: str | None = None
    # Why it counts as flaky ("passed on retry in 2 runs", "flipped 3 times on main").
    reasons: list[str] = Field(default_factory=list)
    # Runs in which it failed and then passed on a retry.
    retried: int = 0
    # Matches the cycle's quarantine: the list kept on the server, or testosterone.yaml when it is readable.
    quarantined: bool = False
    # The stored entry it matches exactly, which the UI can release (null for config rules and globs).
    quarantine_entry: str | None = None


class BrokenTestItem(ApiModel):
    """Failed each of its last runs: a real failure, kept apart from flaky tests."""

    key: str
    name: str
    group: str
    failing_runs: int
    message: str = ""


class FlakyTestsResponse(ApiModel):
    cycle: str
    window: int
    runs_analyzed: int
    tests_analyzed: int
    items: list[FlakyTestItem] = Field(default_factory=list)
    broken: list[BrokenTestItem] = Field(default_factory=list)


class TestSummaryItem(ApiModel):
    """One test across a cycle's recent runs (``testo_core/services/test_history.py``)."""

    key: str
    name: str
    full_name: str
    group: str
    stage: str
    runs: int
    passed: int
    failed: int
    last_status: str
    flaky: bool
    last_duration_ms: int | None = None
    median_duration_ms: int | None = None
    # Recent median minus the earlier one, only when that is more than timing noise.
    slowdown_ms: int | None = None
    slowdown_pct: float | None = None
    # From the test's latest run.
    labels: list[TestLabelItem] = Field(default_factory=list)
    links: list[TestLinkItem] = Field(default_factory=list)


class CycleTestsResponse(ApiModel):
    cycle: str
    runs_considered: int
    items: list[TestSummaryItem] = Field(default_factory=list)


class LabelCountItem(ApiModel):
    name: str
    value: str
    tests: int
    # Tests whose latest outcome failed.
    failing: int
    flaky: int
    # What ``GET /analytics/tests?label=`` takes to list these tests.
    filter: str


class CycleLabelsResponse(ApiModel):
    cycle: str
    runs_considered: int
    labelled_tests: int
    total_tests: int
    items: list[LabelCountItem] = Field(default_factory=list)


class TestPointItem(ApiModel):
    run_id: str
    created_at: float
    status: str
    duration_ms: int | None = None
    message: str = ""
    branch: str | None = None
    # Rolling flip rate (0-1) over the runs ending here; null until two passed or failed.
    flip_rate: float | None = None
    # What the run tested on ("browser=firefox, os=linux"), when it recorded one.
    environment: str | None = None


class EnvironmentOutcomeItem(ApiModel):
    """The test on one environment across the window."""

    environment: str
    runs: int
    passed: int
    failed: int
    last_status: str


class AttachmentItem(ApiModel):
    name: str
    content_type: str
    kind: Literal["image", "video", "trace", "text", "file"]
    # Relative to the API root (``history/<run_id>/...``), like report links.
    url: str
    size_bytes: int


class TestHistoryResponse(ApiModel):
    cycle: str
    test: TestSummaryItem
    # Newest first.
    points: list[TestPointItem] = Field(default_factory=list)
    last_failure: TestPointItem | None = None
    # Screenshots, videos, traces the last failing run kept for this test.
    last_failure_attachments: list[AttachmentItem] = Field(default_factory=list)
    # Pass and fail counts per environment, most failures first; empty when no run recorded one.
    environments: list[EnvironmentOutcomeItem] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    issue_url: str | None = None


class DashboardHeadlineKpis(ApiModel):
    latest_run_id: str | None = None
    latest_status: str | None = None
    health_pct: float | None = None
    pass_count: int | None = None
    fail_count: int | None = None
    duration_ms: float | None = None
    # Trends compare the latest run with the previous run of the same cycle;
    # baseline_run_id is null when this is the cycle's first run.
    cycle: str | None = None
    baseline_run_id: str | None = None


class DashboardTrendIndicator(ApiModel):
    direction: Literal["up", "down", "flat", "unknown"]
    delta_abs: float | None = None
    delta_pct: float | None = None


class DashboardRollupSummaryResponse(ApiModel):
    regressions: int
    improvements: int
    unchanged: int
    unknown: int


class DashboardRollupResponse(ApiModel):
    status_summary: DashboardRollupSummaryResponse
    top_highlights: list[str] = Field(default_factory=list)


class DashboardReportLinkResponse(ApiModel):
    url: str | None = None
    state: Literal["available", "missing", "unknown"]


class DashboardReportLinksResponse(ApiModel):
    allure: DashboardReportLinkResponse
    behave: DashboardReportLinkResponse


class DashboardRecentRunItem(ApiModel):
    run_id: str
    cycle: str | None = None
    created_at: float
    status: str | None = None
    returncode: int
    health_pct: float | None = None
    duration_ms: float | None = None
    run_detail_url: str
    compare_url: str | None = None
    branch: str | None = None
    commit: str | None = None


class DashboardDataFreshnessResponse(ApiModel):
    generated_at: float
    source_window_size: int
    degraded: bool
    notes: list[str] = Field(default_factory=list)


class DashboardOverviewResponse(ApiModel):
    headline_kpis: DashboardHeadlineKpis
    trend_indicators: dict[Literal["health", "failed_count", "duration"], DashboardTrendIndicator]
    reliability_rollup: DashboardRollupResponse
    performance_rollup: DashboardRollupResponse
    report_links: DashboardReportLinksResponse
    recent_runs: list[DashboardRecentRunItem] = Field(default_factory=list)
    data_freshness: DashboardDataFreshnessResponse


class DashboardRecentRunsResponse(ApiModel):
    items: list[DashboardRecentRunItem] = Field(default_factory=list)
    generated_at: float


class AttentionFailingCycle(ApiModel):
    cycle: str
    run_id: str
    created_at: float
    branch: str | None = None
    failed_tests: int
    new_failures: int
    failing_runs: int


class AttentionTestItem(ApiModel):
    cycle: str
    key: str
    name: str
    slowdown_ms: int | None = None
    slowdown_pct: float | None = None


class DashboardAttentionResponse(ApiModel):
    """What needs someone now: red cycles, tests that just turned flaky, tests that got slower."""

    cycles: int
    passing_cycles: int
    failing: list[AttentionFailingCycle] = Field(default_factory=list)
    newly_flaky: list[AttentionTestItem] = Field(default_factory=list)
    slower: list[AttentionTestItem] = Field(default_factory=list)


class HealthLiveResponse(ApiModel):
    status: Literal["ok"]


class VersionResponse(ApiModel):
    version: str
    # Newest testo-core on PyPI; null when unknown (offline, CI, TESTO_NO_UPDATE_CHECK=1).
    latest: str | None = None
    update_available: bool = False


class ReadinessCheck(ApiModel):
    status: Literal["ok", "degraded"]
    detail: str | None = None


class HealthReadyResponse(ApiModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, ReadinessCheck]


class AiConfigUpdateRequest(ApiModel):
    enabled: bool = False
    provider: Literal["openai", "anthropic", "openai_compatible", "claude_code"] = "openai"
    model: str = "gpt-4.1-mini"
    api_key_source: Literal["env", "runtime_input"] = "env"
    api_key_env_var: str | None = None
    api_key_input: str | None = None
    timeout_s: float = 8.0
    retry_count: int = 1
    max_input_chars: int = 16000
    max_output_tokens: int = 300
    base_url: str | None = None
    # Also send a short excerpt of each failing test's own source with the summary prompt.
    share_test_source: bool = False


class AiConfigStatusResponse(ApiModel):
    enabled: bool
    configured: bool
    provider: Literal["openai", "anthropic", "openai_compatible", "claude_code"]
    model: str
    api_key_source: Literal["env", "runtime_input"]
    api_key_env_var: str | None = None
    key_present: bool | None = None
    timeout_s: float
    retry_count: int
    max_input_chars: int
    max_output_tokens: int
    base_url: str | None = None
    share_test_source: bool = False


class AiSummaryResponse(ApiModel):
    schema_version: Literal["v1"]
    run_id: str
    status: Literal["available", "no_summary_generated"]
    summary_text: str | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    limitations: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    generated_at: float
    context_stats: dict[str, int] = Field(default_factory=dict)
    error_code: str | None = None


class GenerateAiSummaryRequest(ApiModel):
    force_refresh: bool = False


class IngestResponse(ApiModel):
    """A recorded upload (``POST /api/v1/ingest``, what ``testo ingest --server`` sends)."""

    run_id: str | None = None
    cycle: str
    exit_code: int
    tests: int
    passed: int
    failed: int
    skipped: int
    run_url: str | None = None


class ResourceHolder(ApiModel):
    """Who holds (or asks for) a key. On a server with accounts, ``user`` is the account."""

    user: str = Field(default="", max_length=64)
    host: str = Field(default="", max_length=255)
    pid: int = 0
    run_id: str = Field(default="", max_length=128)
    stage: str = Field(default="", max_length=128)
    worker: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=200)


class ResourceLeaseInfo(ApiModel):
    """A key's current holder, as anyone who can read the server sees it (no lease id)."""

    holder: ResourceHolder
    taken_at: float
    heartbeat_at: float
    expires_at: float
    ttl_s: float
    # Past expires_at the key stays out of the pool this many more seconds.
    reuse_delay_s: float = 0.0


class ResourceLeaseItem(ResourceLeaseInfo):
    """A lease as its holder sees it: ``lease_id`` keeps it alive and gives it back."""

    lease_id: str
    pool: str
    item: str
    labels: list[str] = Field(default_factory=list)
    data: dict[str, str] = Field(default_factory=dict)
    # The previous holder reset the key (its on_release worked).
    clean: bool = False


class ResourceItemStatus(ApiModel):
    item: str
    labels: list[str] = Field(default_factory=list)
    data: dict[str, str] = Field(default_factory=dict)
    disabled: bool = False
    disabled_reason: str = ""
    disabled_by: str | None = None
    lease: ResourceLeaseInfo | None = None
    # Its last lease ended with a successful on_release; cleared when it is taken.
    clean: bool = False


class ResourcePoolStatus(ApiModel):
    name: str
    free: int
    # Requests waiting for a key of this pool right now.
    waiting: int
    items: list[ResourceItemStatus]


class ResourcesResponse(ApiModel):
    pools: list[ResourcePoolStatus]


class ResourceTakeRequest(ApiModel):
    """``POST /api/v1/resources/leases``: what ``testo lease`` and the workers send.

    ``spec`` is the pool from the caller's ``resources:`` config, without its hooks:
    the server only keeps the leases, so it learns pools from the people using them.
    """

    pool: str = Field(min_length=1, max_length=128)
    spec: dict[str, Any]
    labels: list[str] = Field(default_factory=list, max_length=32)
    holder: ResourceHolder = Field(default_factory=ResourceHolder)
    # The same id on every try while waiting keeps the caller's place in the queue.
    waiter_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class ResourceTakeResponse(ApiModel):
    lease: ResourceLeaseItem | None = None
    ahead: int = 0
    free: int = 0
    # busy: ask again; limit: max_per_user reached; no_items: no usable key, waiting won't help.
    reason: Literal["busy", "limit", "no_items"] | None = None


class ResourceItemRequest(ApiModel):
    pool: str = Field(min_length=1, max_length=128)
    item: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=500)


class ResourceActionRequest(ApiModel):
    """``POST /api/v1/resources/actions``: a ``create`` or ``destroy`` command a person ran
    with ``testo pool ... --apply``. The server only keeps this log; it never runs one.
    """

    pool: str = Field(min_length=1, max_length=128)
    item: str = Field(min_length=1, max_length=128)
    verb: Literal["create", "destroy"]
    user: str = Field(default="", max_length=64)
    host: str = Field(default="", max_length=255)
    command: str = Field(default="", max_length=500)
    ok: bool
    detail: str = Field(default="", max_length=500)


class ResourceActionItem(ResourceActionRequest):
    # When the server logged it (its clock, like the lease times).
    at: float


class ResourceActionsResponse(ApiModel):
    actions: list[ResourceActionItem]


class HealthSignalItem(ApiModel):
    key: str
    name: str
    score: int | None = None
    weight: int
    value: str
    how: str


class CycleHealthItem(ApiModel):
    cycle: str
    run_id: str
    created_at: float
    passed_latest: bool
    tests: int
    failing: int
    flaky: int
    slower: int
    green_runs: int
    runs: int
    score: int | None = None


class HealthFindingItem(ApiModel):
    kind: Literal["red_cycle", "flaky", "slower", "quarantined", "skipped"]
    cycle: str
    title: str
    detail: str
    test_key: str | None = None
    run_id: str | None = None


class ProjectHealthResponse(ApiModel):
    """A repeatable 0-100 score of the project under test, from its recorded runs."""

    scope: str | None = None
    score: int | None = None
    grade: str | None = None
    cycles: int
    runs_considered: int
    tests: int
    latest_run_at: float | None = None
    signals: list[HealthSignalItem] = Field(default_factory=list)
    by_cycle: list[CycleHealthItem] = Field(default_factory=list)
    findings: list[HealthFindingItem] = Field(default_factory=list)


class ReviewSkillItem(ApiModel):
    id: str
    name: str
    category: str
    tagline: str
    summary: str
    checks: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    default_on: bool


class ReviewSkillsResponse(ApiModel):
    items: list[ReviewSkillItem]


class ReviewFindingItem(ApiModel):
    severity: Literal["high", "medium", "low"]
    title: str
    detail: str


class SkillResultItem(ApiModel):
    skill_id: str
    name: str
    score: int | None = None
    summary: str
    findings: list[ReviewFindingItem] = Field(default_factory=list)


class ProjectReviewResponse(ApiModel):
    """An AI estimate of the project's tests, one result per enabled review skill."""

    scope: str | None = None
    status: Literal["available", "not_generated", "failed"]
    score: int | None = None
    grade: str | None = None
    summary: str | None = None
    skills: list[SkillResultItem] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    generated_at: float | None = None
    based_on_run_id: str | None = None
    stale: bool = False
    error_code: str | None = None
    error_message: str | None = None
    context_stats: dict[str, int] = Field(default_factory=dict)


class GenerateProjectReviewRequest(ApiModel):
    cycle: str | None = Field(None, max_length=200)
    skills: list[str] = Field(..., max_length=20)


class AskTurnItem(ApiModel):
    question: str = Field(..., max_length=500)
    answer: str = Field(..., max_length=4000)


class AskRequest(ApiModel):
    question: str = Field(..., min_length=1, max_length=500)
    cycle: str | None = Field(None, max_length=200)
    # The conversation so far, oldest first; only the last few turns are used.
    history: list[AskTurnItem] = Field(default_factory=list, max_length=20)


class AskResponse(ApiModel):
    """An AI answer to a question about the recorded test history."""

    status: Literal["answered", "failed"]
    question: str
    scope: str | None = None
    answer: str | None = None
    provider: str | None = None
    model: str | None = None
    answered_at: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    context_stats: dict[str, int] = Field(default_factory=dict)


class ReportKpis(ApiModel):
    health_score: int | None
    health_grade: str | None
    pass_rate: float | None
    prev_pass_rate: float | None
    runs: int
    prev_runs: int
    green: int
    prev_green: int
    coverage_pct: float | None
    prev_coverage_pct: float | None
    cycles: int
    failing_tests: int
    newly_flaky: int
    fixed: int


class ReportTrendPoint(ApiModel):
    day: str
    runs: int
    green: int
    pass_rate: float | None


class ReportCycleRow(ApiModel):
    cycle: str
    runs: int
    green: int
    pass_rate: float | None
    prev_pass_rate: float | None
    failing: int
    newly_flaky: int
    fixed: int
    slower: int
    health_score: int | None


class ReportTest(ApiModel):
    cycle: str
    name: str
    detail: str = ""


class ReportFinding(ApiModel):
    cycle: str
    title: str
    detail: str


class ManagerReportResponse(ApiModel):
    """How testing went over a period: the ``/report`` page and every ``testo export`` format."""

    title: str
    period: Literal["day", "week", "month", "quarter"]
    days: int
    start: float
    end: float
    generated_at: float
    scope: list[str]
    summary: list[str]
    kpis: ReportKpis
    trend: list[ReportTrendPoint]
    cycles: list[ReportCycleRow]
    still_failing: list[ReportTest]
    newly_flaky: list[ReportTest]
    fixed: list[ReportTest]
    slower: list[ReportTest]
    findings: list[ReportFinding]
