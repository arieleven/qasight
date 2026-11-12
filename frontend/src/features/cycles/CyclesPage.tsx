import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { apiClient, type CycleSummary } from "../../lib/api-client";
import { Badge, Button, Card, EmptyState, HealthBar, PageHeader, Spinner, StatusPill, buttonClasses } from "../../components/ui";
import { formatPercent, formatProvenance, formatRelativeTime } from "../../lib/format";
import { MuscleShrug } from "../../components/mascot";
import { IS_STATIC_BUILD, STATIC_LABEL } from "../../lib/static-backend";

export function CyclesPage() {
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ["cycles"], queryFn: () => apiClient.listCycles() });

  if (query.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-ink-300">
        <Spinner /> Loading cycles…
      </div>
    );
  }

  if (query.isError) {
    return (
      <EmptyState
        mascot={<MuscleShrug />}
        title="Couldn't load cycles"
        message={
          <>
            Check that the API is running and that qasight.yaml is valid with{" "}
            <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-xs text-brand-300">
              qasight config validate
            </code>
            , then reload.
          </>
        }
      />
    );
  }

  const cycles = query.data?.items ?? [];

  return (
    <section>
      <PageHeader
        title="Cycles"
        subtitle={
          IS_STATIC_BUILD
            ? "The cycles that ran for this snapshot."
            : query.data?.config_path
              ? `Defined in ${query.data.config_path}, plus cycles that only have recorded runs.`
              : "Cycles with recorded runs."
        }
        actions={
          IS_STATIC_BUILD ? undefined : (
            <Link to="/quick-run" className={buttonClasses("secondary", "sm")} title="Run one framework directly, without a cycle">
              Quick run
            </Link>
          )
        }
      />
      {cycles.length === 0 ? (
        <EmptyState
          mascot={<MuscleShrug />}
          title="No cycles yet"
          message={
            <>
              Record a run with{" "}
              <code className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-xs text-brand-300">
                qasight ingest &apos;reports/**/*.xml&apos; --cycle my-tests
              </code>{" "}
              or add a cycle to qasight.yaml.
            </>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {cycles.map((cycle) => {
            const recorded = cycle.source === "history";
            return (
              <Card key={cycle.name} className="flex flex-col justify-between gap-3">
                <div>
                  <Link
                    to={`/cycles/${encodeURIComponent(cycle.name)}`}
                    className="font-mono text-sm font-semibold text-brand-300 hover:text-brand-400 hover:underline"
                  >
                    {cycle.name}
                  </Link>
                  <p className="mt-1.5 line-clamp-2 text-sm text-ink-300">
                    {cycle.description ?? "No description."}
                  </p>
                  {IS_STATIC_BUILD && !recorded ? (
                    <p className="mt-2 text-xs text-ink-400">
                      Run locally with{" "}
                      <code className="font-mono text-ink-200">qasight run --cycle {cycle.name}</code>
                    </p>
                  ) : null}
                </div>
                <LatestRun cycle={cycle.name} run={cycle.latest_run} />
                <div className="flex items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge>
                      {cycle.stage_count} stage{cycle.stage_count === 1 ? "" : "s"}
                    </Badge>
                    {cycle.equipment.map((eq) => (
                      <Badge key={eq} tone="brand">
                        {eq}
                      </Badge>
                    ))}
                  </div>
                  {recorded ? (
                    <Badge>Recorded</Badge>
                  ) : IS_STATIC_BUILD ? (
                    // A disabled button gets no hover events, so the wrapper carries the tooltip.
                    <span title={`${STATIC_LABEL}. Run locally with: qasight run --cycle ${cycle.name}`}>
                      <Button size="sm" variant="secondary" disabled>
                        Run
                      </Button>
                    </span>
                  ) : (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => navigate(`/cycles/${encodeURIComponent(cycle.name)}?run=1`)}
                    >
                      Run
                    </Button>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** The cycle's latest result: status, pass rate, when, and where it came from; or that it has never run. */
function LatestRun({ cycle, run }: { cycle: string; run: CycleSummary["latest_run"] }) {
  if (!run) {
    return <p className="text-xs text-ink-400">No runs recorded yet.</p>;
  }
  const provenance = formatProvenance(run);
  const failing = run.failed != null && run.failed > 0 ? `${run.failed} failing` : null;
  return (
    <div className="space-y-1.5" data-testid={`latest-run-${cycle}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-400">
        <StatusPill status={run.status ?? "unknown"} returncode={run.returncode} />
        <Link to={`/runs/${run.run_id}`} className="text-brand-300 hover:underline" title={new Date(run.created_at * 1000).toLocaleString()}>
          {formatRelativeTime(run.created_at)}
        </Link>
        {run.health_pct != null ? <span>{formatPercent(run.health_pct)} passed</span> : null}
        {failing ? <span className="text-danger-300">{failing}</span> : null}
        {provenance ? <span className="font-mono text-ink-300">{provenance}</span> : null}
      </div>
      <HealthBar healthPct={run.health_pct ?? null} />
    </div>
  );
}
