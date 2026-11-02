import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { API_BASE, apiClient } from "../../lib/api-client";
import { Badge, Button, Card, KeyValue, PageHeader, Spinner, StatusPill, TestPyramid, buttonClasses } from "../../components/ui";
import { countLabel, describeExitCode, formatDuration, formatPercent, formatRunLabel } from "../../lib/format";
import { AI_SUMMARY_SNAPSHOT_MESSAGE, IS_STATIC_BUILD } from "../../lib/static-backend";
import { CopySummaryButton } from "./CopySummaryButton";
import { CrashBadge } from "./CrashBadge";
import { FailingTestsCard } from "./FailingTestsCard";

type RunProvenanceFields = {
  branch?: string | null;
  commit?: string | null;
  base_branch?: string | null;
  repository?: string | null;
};

/** Branch, PR target and commit the run tested, when it recorded them. */
function RunProvenance({ run }: { run: RunProvenanceFields }) {
  if (!run.branch && !run.commit) return null;
  const short = run.commit ? run.commit.slice(0, 7) : null;
  const commitUrl =
    run.commit && run.repository && /^[\w.-]+\/[\w.-]+$/.test(run.repository)
      ? `https://github.com/${run.repository}/commit/${run.commit}`
      : null;
  return (
    <span className="mt-1 flex flex-wrap items-center gap-x-1.5 text-xs text-ink-400">
      {run.branch ? <span className="font-mono text-ink-200">{run.branch}</span> : null}
      {run.base_branch && run.base_branch !== run.branch ? (
        <span>
          into <span className="font-mono text-ink-200">{run.base_branch}</span>
        </span>
      ) : null}
      {short ? (
        <>
          {run.branch ? <span aria-hidden>·</span> : null}
          {commitUrl ? (
            <a href={commitUrl} className="font-mono text-brand-300 hover:underline" title={run.commit ?? undefined}>
              {short}
            </a>
          ) : (
            <span className="font-mono" title={run.commit ?? undefined}>
              {short}
            </span>
          )}
        </>
      ) : null}
    </span>
  );
}

// Error codes that the user fixes on the AI settings page.
/** How many tiers hold tests: a mix needs at least two. */
function tierCount(mix: { unit: number; integration: number; e2e: number }): number {
  return [mix.unit, mix.integration, mix.e2e].filter((n) => n > 0).length;
}

const AI_SETTINGS_ERROR_CODES = new Set(["ai_feature_disabled", "provider_misconfigured", "unsupported_provider_model"]);

const AI_SUMMARY_ERROR_MESSAGES: Record<string, string> = {
  summary_not_available: "No summary has been generated for this run yet.",
  ai_feature_disabled: "AI failure summaries are switched off.",
  provider_misconfigured: "No summary generated: the AI provider has no API key, or rejected it.",
  unsupported_provider_model: "No summary generated: the configured model is not available for this API key.",
  provider_timeout: "No summary generated: the AI provider timed out. Try again.",
  provider_rate_limited: "No summary generated: the AI provider rate-limited the request. Try again shortly."
};

function describeAiSummaryError(code: string | null | undefined, limitations: string[] = []): string {
  // Limitation codes are snake_case; a provider failure is recorded as a sentence.
  const providerError = limitations.find((item) => item.includes(" "));
  if (code === "summary_not_available" && providerError) {
    return `No summary generated: ${providerError}`;
  }
  return AI_SUMMARY_ERROR_MESSAGES[code ?? ""] ?? `No summary generated (${code ?? "not_available"}).`;
}

// The labels the summary prompt asks for (testo_core/services/failure_context_builder.py).
const AI_SUMMARY_LABEL = /^(What failed|Likely cause|Next steps):\s*(.*)$/;

function AiSummaryText({ text }: { text: string }) {
  return (
    <div className="space-y-1 text-sm text-ink-100">
      {text
        .split("\n")
        .filter((line) => line.trim())
        .map((line, index) => {
          const labelled = AI_SUMMARY_LABEL.exec(line);
          return labelled ? (
            <p key={index} className="pt-1">
              <span className="font-semibold text-ink-50">{labelled[1]}:</span> {labelled[2]}
            </p>
          ) : (
            <p key={index} className="whitespace-pre-wrap">
              {line}
            </p>
          );
        })}
    </div>
  );
}

/** One line under the title that answers "how did it go?" before anything else. */
function runVerdict(run: { returncode: number | null; total_tests?: number | null; failed?: number | null; broken?: number | null; wall_duration_ms: number }): string {
  const total = run.total_tests ?? 0;
  const failing = (run.failed ?? 0) + (run.broken ?? 0);
  const took = `in ${formatDuration(run.wall_duration_ms)}`;
  if (total === 0) return run.returncode === 0 ? `Passed ${took}` : `Failed before any test result was recorded, ${took}`;
  if (failing > 0) return `${countLabel(failing, "test")} of ${total} failed ${took}`;
  if (run.returncode === 0) return `All ${countLabel(total, "test")} passed ${took}`;
  return `No test failed, but the run exited ${describeExitCode(run.returncode)}, ${took}`;
}

export function RunDetailPage() {
  const params = useParams();
  const runId = params.runId ?? "";
  const [artifactsExpanded, setArtifactsExpanded] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => apiClient.getRun(runId),
    enabled: Boolean(runId)
  });
  const reportsQuery = useQuery({
    queryKey: ["reports", runId],
    queryFn: () => apiClient.getRunReports(runId),
    enabled: Boolean(runId)
  });
  const aiSummaryQuery = useQuery({
    queryKey: ["run-ai-summary", runId],
    queryFn: () => apiClient.getRunAiSummary(runId),
    enabled: Boolean(runId)
  });
  const pyramidQuery = useQuery({
    queryKey: ["run-pyramid", runId],
    queryFn: () => apiClient.getRunPyramid(runId),
    enabled: Boolean(runId)
  });

  if (!runId) {
    return <p className="text-sm text-danger-400">Missing run id.</p>;
  }
  if (runQuery.isLoading || reportsQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-ink-300">
        <Spinner /> Loading run…
      </div>
    );
  }
  if (runQuery.isError || reportsQuery.isError || !runQuery.data || !reportsQuery.data) {
    return <p className="text-sm text-danger-400">Couldn't load this run. It may have been cleaned up, or the API is unreachable.</p>;
  }

  const run = runQuery.data.run;
  const reports = reportsQuery.data;
  const failed = run.returncode !== 0 && run.returncode != null;
  // Files under the run's snapshot are served by the API (at /history); runs
  // whose snapshot lives elsewhere just list the names.
  const artifactHref = (path: string): string | null =>
    reports.artifacts_url && reports.artifact_links.includes(path)
      ? `${API_BASE}/${reports.artifacts_url}/${path.split("/").map(encodeURIComponent).join("/")}`
      : null;

  return (
    <section className="space-y-4">
      <PageHeader
        documentTitle={formatRunLabel(run)}
        title={
          <span className="flex items-center gap-3">
            <span>{formatRunLabel(run)}</span>
            <StatusPill status={run.status} returncode={run.returncode} />
          </span>
        }
        subtitle={
          <>
            {runVerdict(run)}
            <RunProvenance run={run} />
          </>
        }
        actions={
          <>
            <CopySummaryButton runId={runId} />
            <Link to="/runs" className={buttonClasses("secondary", "sm")}>
              Back to runs
            </Link>
          </>
        }
      />

      {failed || (run.failed ?? 0) + (run.broken ?? 0) > 0 ? <FailingTestsCard runId={runId} cycle={run.cycle} /> : null}

      {failed ? (
        <Card title="AI failure summary">
          {aiSummaryQuery.isLoading ? (
            <p className="text-sm text-ink-300">Loading AI summary…</p>
          ) : aiSummaryQuery.isError || !aiSummaryQuery.data ? (
            <p className="text-sm text-danger-400">Couldn't load the AI summary.</p>
          ) : aiSummaryQuery.data.status === "available" ? (
            <article className="space-y-1">
              <AiSummaryText text={aiSummaryQuery.data.summary_text ?? ""} />
              <p className="text-xs text-ink-400">
                Confidence: {aiSummaryQuery.data.confidence ?? "unknown"} | Model: {aiSummaryQuery.data.model ?? "unknown"}
                {aiSummaryQuery.data.context_stats?.source_files
                  ? ` | Read the source of ${aiSummaryQuery.data.context_stats.source_files} test file${aiSummaryQuery.data.context_stats.source_files === 1 ? "" : "s"}`
                  : ""}
                {IS_STATIC_BUILD ? " | Generated when this snapshot was built" : ""}
              </p>
            </article>
          ) : IS_STATIC_BUILD ? (
            <p className="text-sm text-ink-300">
              {aiSummaryQuery.data.limitations.includes("summary_not_generated")
                ? AI_SUMMARY_SNAPSHOT_MESSAGE
                : `The AI summary could not be generated when this snapshot was built (${aiSummaryQuery.data.error_code ?? "not_available"}).`}
            </p>
          ) : (
            <p className="text-sm text-ink-400">
              {describeAiSummaryError(aiSummaryQuery.data.error_code, aiSummaryQuery.data.limitations)}
              {AI_SETTINGS_ERROR_CODES.has(aiSummaryQuery.data.error_code ?? "") ? (
                <>
                  {" "}
                  <Link className="text-brand-300 underline" to="/settings/ai">
                    Open AI settings
                  </Link>
                </>
              ) : null}
            </p>
          )}
          <Button
            className="mt-3"
            // A snapshot has nothing to generate with; the export already did it, or explains why not above.
            disabled={IS_STATIC_BUILD}
            title={IS_STATIC_BUILD ? "Read-only demo: summaries are generated when the snapshot is built." : undefined}
            onClick={async () => {
              setAiError(null);
              try {
                await apiClient.generateRunAiSummary(runId, true);
                await aiSummaryQuery.refetch();
              } catch (err) {
                setAiError(err instanceof Error ? err.message : String(err));
              }
            }}
          >
            Generate AI summary
          </Button>
          {aiError ? <p className="mt-2 text-sm text-danger-400">{aiError}</p> : null}
        </Card>
      ) : null}

      <Card title="Stages">
        {(run.stage_health ?? []).length === 0 ? (
          <p className="text-sm text-ink-400">Per-stage breakdown not available for this run.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {(run.stage_health ?? []).map((stage) => (
              <li key={stage.name} className="space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-ink-100">
                    {stage.name}
                    {stage.framework ? <span className="ml-2 text-xs text-ink-400">{stage.framework}</span> : null}
                  </span>
                  <span className="flex items-center gap-2">
                    <CrashBadge kind={stage.crash_kind} />
                    {artifactHref(`${stage.name}/run.log`) ? (
                      <a
                        href={artifactHref(`${stage.name}/run.log`) ?? undefined}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={`Log of ${stage.name}`}
                        className="text-xs text-brand-300 hover:underline"
                      >
                        Log
                      </a>
                    ) : null}
                    {stage.total_tests != null && (
                      <span className="text-xs text-ink-400">
                        {stage.passed ?? 0}/{stage.total_tests}
                      </span>
                    )}
                    <Badge tone={stage.health_pct == null ? "neutral" : stage.health_pct >= 100 ? "success" : stage.health_pct > 0 ? "warn" : "danger"}>
                      {formatPercent(stage.health_pct)}
                    </Badge>
                  </span>
                </div>
                {stage.crash_detail ? (
                  <p className="break-words font-mono text-xs text-danger-300" data-testid="stage-crash">
                    {stage.crash_detail}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {Object.keys(reports.static_links).length > 0 ? (
        <Card title="Reports">
          <ul className="space-y-1 text-sm">
            {Object.entries(reports.static_links).map(([name, url]) => (
              <li key={name}>
                <a href={`${API_BASE}/${url}`} target="_blank" rel="noreferrer" className="text-brand-300 hover:text-brand-400 hover:underline">
                  {name}
                </a>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {/* A mix only means something with two or more tiers; an ingested run has one, so there is nothing to judge. */}
      {pyramidQuery.isError ? (
        <Card title="Test mix">
          <p className="text-sm text-danger-400">Couldn't load the test mix.</p>
        </Card>
      ) : pyramidQuery.data && tierCount(pyramidQuery.data) >= 2 ? (
        <Card title="Test mix">
          <TestPyramid {...pyramidQuery.data} />
        </Card>
      ) : null}

      <Card title="Details">
        <KeyValue
          items={[
            { label: "Run ID", value: <span className="font-mono text-xs">{run.run_id}</span> },
            { label: "Exit code", value: describeExitCode(run.returncode) },
            { label: "Pass rate", value: run.total_tests ? formatPercent(run.health_pct) : "No tests ran" },
            ...(run.coverage_pct != null
              ? [
                  {
                    label: "Line coverage",
                    value: `${formatPercent(run.coverage_pct)}${
                      run.coverage_total != null ? ` (${run.coverage_covered ?? 0} of ${run.coverage_total} lines)` : ""
                    }`
                  }
                ]
              : []),
            { label: "Duration", value: formatDuration(run.wall_duration_ms) },
            ...(Object.keys(reports.static_links).length === 0
              ? [{ label: "Reports", value: <span className="text-ink-400">No reports for this run.</span> }]
              : [])
          ]}
        />
      </Card>

      <Card
        title={
          <button
            type="button"
            aria-expanded={artifactsExpanded}
            onClick={() => setArtifactsExpanded((expanded) => !expanded)}
            className="flex items-center gap-2 text-sm font-semibold text-ink-100"
          >
            <span className={`transition-transform ${artifactsExpanded ? "rotate-90" : ""}`}>
              &#9656;
            </span>
            Artifacts
          </button>
        }
      >
        {artifactsExpanded &&
          (reports.artifact_links.length === 0 ? (
            <p className="text-sm text-ink-400">No artifacts recorded.</p>
          ) : (
            <ul className="space-y-1 text-sm text-ink-300">
              {reports.artifact_links.map((artifact) => {
                const href = artifactHref(artifact);
                return (
                  <li key={artifact} className="font-mono text-xs">
                    {href ? (
                      <a href={href} target="_blank" rel="noreferrer" className="break-all text-brand-300 hover:underline">
                        {artifact}
                      </a>
                    ) : (
                      <span className="break-all">{artifact}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          ))}
      </Card>

    </section>
  );
}
