// Request/response types are generated from the FastAPI OpenAPI schema, so the
// backend's Pydantic models in testo_api/models.py are the single source of
// truth for the HTTP contract. To regenerate after changing a model:
//   python scripts/export_openapi.py && npm --prefix frontend run gen:api
// CI fails if either generated file is stale.
import type { components } from "./api-schema";

type Schemas = components["schemas"];

export type AdhocExecutionRequest = Schemas["AdhocExecutionRequest"];
export type AdhocFramework = AdhocExecutionRequest["framework"];

export type CycleExecutionRequest = Schemas["CycleExecutionRequest"];
export type CycleExecutionAccepted = Schemas["CycleExecutionAcceptedResponse"];
export type CycleExecutionStatus = Schemas["CycleExecutionStatusResponse"];
export type CycleSummary = Schemas["CycleSummary"];
export type CycleListResponse = Schemas["CycleListResponse"];
export type StageSummary = Schemas["StageSummary"];
export type CycleDetailResponse = Schemas["CycleDetailResponse"];

export type RunListItem = Schemas["RunListItem"];
export type RunListResponse = Schemas["RunListResponse"];
export type StageHealth = Schemas["StageHealth"];
export type RunDetailResponse = Schemas["RunDetailResponse"];
export type RunReportsResponse = Schemas["RunReportsResponse"];
export type RunPyramidResponse = Schemas["RunPyramidResponse"];
export type RunMarkdownResponse = Schemas["RunMarkdownResponse"];
export type ManagerReport = Schemas["ManagerReportResponse"];
export type ReportPeriod = ManagerReport["period"];
export type RunFailuresResponse = Schemas["RunFailuresResponse"];
export type FailingTestItem = Schemas["FailingTestItem"];
export type TriageItem = Schemas["TriageItem"];
export type TriageRequest = Schemas["TriageRequest"];
export type TriageCause = TriageItem["cause"];
export type QuarantineItem = Schemas["QuarantineItem"];
export type QuarantineRequest = Schemas["QuarantineRequest"];
export type QuarantineListResponse = Schemas["QuarantineListResponse"];
export type PyramidShape = RunPyramidResponse["shape"];
export type VersionResponse = Schemas["VersionResponse"];

export type DeltaMetricNode = Schemas["DeltaMetricNode"];
export type DeltaClassification = DeltaMetricNode["classification"];
export type DeltaComparisonResponse = Schemas["DeltaComparisonResponse"];
export type DeltaStageDelta = Schemas["DeltaStageDelta"];
export type DeltaCaseChange = Schemas["DeltaCaseChange"];
export type CaseChangeKind = DeltaCaseChange["kind"];
export type DeltaCaseChangesResponse = Schemas["DeltaCaseChangesResponse"];
export type FlakyTestItem = Schemas["FlakyTestItem"];
export type FlakyTestsResponse = Schemas["FlakyTestsResponse"];
export type TestSummaryItem = Schemas["TestSummaryItem"];
export type CycleTestsResponse = Schemas["CycleTestsResponse"];
export type TestLabelItem = Schemas["TestLabelItem"];
export type EnvironmentOutcomeItem = Schemas["EnvironmentOutcomeItem"];
export type TestLinkItem = Schemas["TestLinkItem"];
export type CycleLabelsResponse = Schemas["CycleLabelsResponse"];
export type LabelCountItem = Schemas["LabelCountItem"];
export type ReleasesResponse = Schemas["ReleasesResponse"];
export type ReleaseSummaryItem = Schemas["ReleaseSummaryItem"];
export type ReleaseDetailResponse = Schemas["ReleaseDetailResponse"];
export type ReleaseTestItem = Schemas["ReleaseTestItem"];
export type TestPointItem = Schemas["TestPointItem"];
export type TestHistoryResponse = Schemas["TestHistoryResponse"];
export type AttachmentItem = Schemas["AttachmentItem"];

export type AiConfigStatus = Schemas["AiConfigStatusResponse"];
export type UpdateAiConfigRequest = Schemas["AiConfigUpdateRequest"];
export type AiSummaryResponse = Schemas["AiSummaryResponse"];

export type DashboardTrendIndicator = Schemas["DashboardTrendIndicator"];
export type DashboardOverviewResponse = Schemas["DashboardOverviewResponse"];
export type DashboardRecentRunsResponse = Schemas["DashboardRecentRunsResponse"];
export type DashboardAttentionResponse = Schemas["DashboardAttentionResponse"];

export type ResourcesResponse = Schemas["ResourcesResponse"];
export type ResourcePoolStatus = Schemas["ResourcePoolStatus"];
export type ResourceItemStatus = Schemas["ResourceItemStatus"];
export type ResourceLeaseInfo = Schemas["ResourceLeaseInfo"];
export type ResourceActionItem = Schemas["ResourceActionItem"];
export type ResourceActionsResponse = Schemas["ResourceActionsResponse"];
export type ProjectHealthResponse = Schemas["ProjectHealthResponse"];
export type HealthSignalItem = Schemas["HealthSignalItem"];
export type HealthFindingItem = Schemas["HealthFindingItem"];
export type ReviewSkillItem = Schemas["ReviewSkillItem"];
export type ReviewSkillsResponse = Schemas["ReviewSkillsResponse"];
export type ProjectReviewResponse = Schemas["ProjectReviewResponse"];
export type AskResponse = Schemas["AskResponse"];
export type AskTurnItem = Schemas["AskTurnItem"];
export type SkillResultItem = Schemas["SkillResultItem"];

export type HealthReadyResponse = Schemas["HealthReadyResponse"];
export type WhoAmI = Schemas["WhoAmIResponse"];

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// Only needed when the API runs with TESTO_API_TOKEN (it then rejects writes without
// it). Set at build time, or at runtime with localStorage.setItem("testo.apiToken", …).
// The UI a team server serves needs neither: signing in at /login sets a session cookie.
function apiToken(): string | null {
  if (import.meta.env.VITE_TESTO_API_TOKEN) return import.meta.env.VITE_TESTO_API_TOKEN;
  try {
    return localStorage.getItem("testo.apiToken");
  } catch {
    return null; // storage disabled (privacy mode, sandboxed iframe)
  }
}

/** A non-2xx answer from the API, with its HTTP status. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * React Query retry policy: a 4xx (missing run, bad input) will answer the same
 * again, so show it at once; network errors and 5xx get two more tries.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = apiToken();
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers as Record<string, string> | undefined),
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    }
  });
  if (resp.status === 401 && API_BASE === "" && !token) {
    // Served by a team server that protects reads (TESTO_PROTECT_READS): the session
    // cookie is missing or stale, so sign in and come back here.
    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
  }
  if (!resp.ok) {
    // The API answers errors with {error: {code, message}}; surfacing that message
    // beats "API error 405" in the UI (and tells visitors of the read-only static
    // build why an action did nothing).
    const body = (await resp.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new ApiError(body?.error?.message ?? `API error ${resp.status}`, resp.status);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** A file from the API (a report export): its bytes and the name the server suggested. */
async function apiFile(path: string): Promise<{ blob: Blob; filename: string | null }> {
  const token = apiToken();
  const resp = await fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!resp.ok) {
    const body = (await resp.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new ApiError(body?.error?.message ?? `API error ${resp.status}`, resp.status);
  }
  const disposition = resp.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? null;
  return { blob: await resp.blob(), filename };
}

export type ReportFormat = "pdf" | "png" | "html" | "pptx" | "xlsx" | "csv" | "json";

export const apiClient = {
  createCycleExecution(cycle: string, payload: CycleExecutionRequest): Promise<CycleExecutionAccepted> {
    return api<CycleExecutionAccepted>(`/api/v1/cycles/${encodeURIComponent(cycle)}/executions`, {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  markTriage(payload: TriageRequest): Promise<TriageItem> {
    return api<TriageItem>("/api/v1/triage", { method: "PUT", body: JSON.stringify(payload) });
  },
  clearTriage(cycle: string, signature: string): Promise<void> {
    const query = new URLSearchParams({ cycle, signature });
    return api<void>(`/api/v1/triage?${query}`, { method: "DELETE" });
  },
  listQuarantine(cycle: string): Promise<QuarantineListResponse> {
    return api<QuarantineListResponse>(`/api/v1/quarantine?${new URLSearchParams({ cycle })}`);
  },
  quarantineTest(payload: QuarantineRequest): Promise<QuarantineItem> {
    return api<QuarantineItem>("/api/v1/quarantine", { method: "PUT", body: JSON.stringify(payload) });
  },
  releaseTest(cycle: string, test: string): Promise<void> {
    const query = new URLSearchParams({ cycle, test });
    return api<void>(`/api/v1/quarantine?${query}`, { method: "DELETE" });
  },
  createAdhocExecution(payload: AdhocExecutionRequest): Promise<CycleExecutionAccepted> {
    return api<CycleExecutionAccepted>("/api/v1/adhoc-executions", {
      method: "POST",
      body: JSON.stringify(payload)
    });
  },
  getCycleExecutionStatus(executionId: string): Promise<CycleExecutionStatus> {
    return api<CycleExecutionStatus>(`/api/v1/cycle-executions/${executionId}`);
  },
  listCycles(): Promise<CycleListResponse> {
    return api<CycleListResponse>("/api/v1/cycles");
  },
  getCycle(name: string): Promise<CycleDetailResponse> {
    return api<CycleDetailResponse>(`/api/v1/cycles/${encodeURIComponent(name)}`);
  },
  async getHealthReady(): Promise<HealthReadyResponse> {
    // /health/ready responds 503 when degraded but still carries the check payload.
    const resp = await fetch(`${API_BASE}/api/v1/health/ready`, {
      headers: { "Content-Type": "application/json" }
    });
    return (await resp.json()) as HealthReadyResponse;
  },
  getWhoAmI(): Promise<WhoAmI> {
    return api<WhoAmI>("/api/v1/auth/me");
  },
  async listRuns(cycle?: string, limit = 30): Promise<RunListResponse> {
    const params = new URLSearchParams({ limit: String(limit), ...(cycle ? { cycle } : {}) });
    const data = await api<RunListResponse>(`/api/v1/runs?${params}`);
    // The static build answers every /runs request with the full list; keep only this cycle's.
    return { ...data, items: data.items.filter((run) => !cycle || run.cycle === cycle).slice(0, limit) };
  },
  getRun(runId: string): Promise<RunDetailResponse> {
    return api<RunDetailResponse>(`/api/v1/runs/${encodeURIComponent(runId)}`);
  },
  getRunReports(runId: string): Promise<RunReportsResponse> {
    return api<RunReportsResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/reports`);
  },
  getRunFailures(runId: string): Promise<RunFailuresResponse> {
    return api<RunFailuresResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/failures`);
  },
  getReport(period: ReportPeriod, cycle?: string): Promise<ManagerReport> {
    const params = new URLSearchParams({ period, ...(cycle ? { cycle } : {}) });
    return api<ManagerReport>(`/api/v1/report?${params}`);
  },
  downloadReport(format: ReportFormat, period: ReportPeriod, cycle?: string): Promise<{ blob: Blob; filename: string | null }> {
    const params = new URLSearchParams({ format, period, ...(cycle ? { cycle } : {}) });
    return apiFile(`/api/v1/report/export?${params}`);
  },
  getRunMarkdown(runId: string): Promise<RunMarkdownResponse> {
    return api<RunMarkdownResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/markdown`);
  },
  getRunPyramid(runId: string): Promise<RunPyramidResponse> {
    return api<RunPyramidResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/pyramid`);
  },
  getDeltaComparison(currentRunId: string, baselineRunId: string): Promise<DeltaComparisonResponse> {
    const params = new URLSearchParams({
      current_run_id: currentRunId,
      baseline_run_id: baselineRunId
    });
    return api<DeltaComparisonResponse>(`/api/v1/analytics/delta?${params.toString()}`);
  },
  getDeltaCaseChanges(currentRunId: string, baselineRunId: string): Promise<DeltaCaseChangesResponse> {
    const params = new URLSearchParams({
      current_run_id: currentRunId,
      baseline_run_id: baselineRunId
    });
    return api<DeltaCaseChangesResponse>(`/api/v1/analytics/delta/cases?${params.toString()}`);
  },
  getFlakyTests(cycle: string): Promise<FlakyTestsResponse> {
    const params = new URLSearchParams({ cycle });
    return api<FlakyTestsResponse>(`/api/v1/analytics/flaky?${params.toString()}`);
  },
  getCycleTests(cycle: string): Promise<CycleTestsResponse> {
    const params = new URLSearchParams({ cycle });
    return api<CycleTestsResponse>(`/api/v1/analytics/tests?${params.toString()}`);
  },
  getReleases(): Promise<ReleasesResponse> {
    return api<ReleasesResponse>("/api/v1/releases");
  },
  getRelease(release: string): Promise<ReleaseDetailResponse> {
    return api<ReleaseDetailResponse>(`/api/v1/releases/${encodeURIComponent(release)}`);
  },
  getCycleLabels(cycle: string): Promise<CycleLabelsResponse> {
    const params = new URLSearchParams({ cycle });
    return api<CycleLabelsResponse>(`/api/v1/analytics/labels?${params.toString()}`);
  },
  getTestHistory(cycle: string, key: string): Promise<TestHistoryResponse> {
    const params = new URLSearchParams({ cycle, key });
    return api<TestHistoryResponse>(`/api/v1/analytics/test-history?${params.toString()}`);
  },
  getDashboardOverview(recentLimit = 5): Promise<DashboardOverviewResponse> {
    return api<DashboardOverviewResponse>(`/api/v1/dashboard/overview?recent_limit=${recentLimit}`);
  },
  getDashboardRecentRuns(limit = 10): Promise<DashboardRecentRunsResponse> {
    return api<DashboardRecentRunsResponse>(`/api/v1/dashboard/runs/recent?limit=${limit}`);
  },
  listResources(): Promise<ResourcesResponse> {
    return api<ResourcesResponse>("/api/v1/resources");
  },
  listResourceActions(limit = 20): Promise<ResourceActionsResponse> {
    return api<ResourceActionsResponse>(`/api/v1/resources/actions?limit=${limit}`);
  },
  disableResource(pool: string, item: string, reason: string): Promise<void> {
    return api<void>("/api/v1/resources/items/disable", {
      method: "POST",
      body: JSON.stringify({ pool, item, reason })
    });
  },
  enableResource(pool: string, item: string): Promise<void> {
    return api<void>("/api/v1/resources/items/enable", { method: "POST", body: JSON.stringify({ pool, item }) });
  },
  freeResource(pool: string, item: string): Promise<void> {
    return api<void>("/api/v1/resources/items/release", { method: "POST", body: JSON.stringify({ pool, item }) });
  },
  getVersion(): Promise<VersionResponse> {
    return api<VersionResponse>("/api/v1/version");
  },
  getDashboardAttention(): Promise<DashboardAttentionResponse> {
    return api<DashboardAttentionResponse>("/api/v1/dashboard/attention");
  },
  getProjectHealth(cycle?: string): Promise<ProjectHealthResponse> {
    const query = cycle ? `?${new URLSearchParams({ cycle })}` : "";
    return api<ProjectHealthResponse>(`/api/v1/project-health${query}`);
  },
  listReviewSkills(): Promise<ReviewSkillsResponse> {
    return api<ReviewSkillsResponse>("/api/v1/project-health/skills");
  },
  getProjectReview(cycle?: string): Promise<ProjectReviewResponse> {
    const query = cycle ? `?${new URLSearchParams({ cycle })}` : "";
    return api<ProjectReviewResponse>(`/api/v1/project-health/ai-review${query}`);
  },
  generateProjectReview(cycle: string | undefined, skills: string[]): Promise<ProjectReviewResponse> {
    return api<ProjectReviewResponse>("/api/v1/project-health/ai-review", {
      method: "POST",
      body: JSON.stringify({ cycle: cycle ?? null, skills })
    });
  },
  askAboutTests(question: string, cycle: string | undefined, history: AskTurnItem[]): Promise<AskResponse> {
    return api<AskResponse>("/api/v1/ask", {
      method: "POST",
      body: JSON.stringify({ question, cycle: cycle ?? null, history })
    });
  },
  getAiConfigStatus(): Promise<AiConfigStatus> {
    return api<AiConfigStatus>("/api/v1/ai/config/status");
  },
  updateAiConfig(payload: UpdateAiConfigRequest): Promise<AiConfigStatus> {
    return api<AiConfigStatus>("/api/v1/ai/config", {
      method: "PUT",
      body: JSON.stringify(payload)
    });
  },
  getRunAiSummary(runId: string): Promise<AiSummaryResponse> {
    return api<AiSummaryResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/ai-summary`);
  },
  generateRunAiSummary(runId: string, forceRefresh = false): Promise<AiSummaryResponse> {
    return api<AiSummaryResponse>(`/api/v1/runs/${encodeURIComponent(runId)}/ai-summary:generate`, {
      method: "POST",
      body: JSON.stringify({ force_refresh: forceRefresh })
    });
  }
};
