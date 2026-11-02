import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { DashboardOverviewResponse, DashboardTrendIndicator, apiClient } from "../../lib/api-client";
import { Card, HealthBar, PageHeader, RunLabel, Spinner, StatusPill } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { formatDuration, formatPercent } from "../../lib/format";
import { DeliveryCard } from "./DeliveryCard";
import { NeedsAttention } from "./NeedsAttention";
import { TONE_TEXT, scoreTone } from "../health/score";

const RECENT_RUNS_REFRESH_MS = 15_000;

export function DashboardPage() {
  const overviewQuery = useQuery({
    queryKey: ["dashboard-overview"],
    queryFn: () => apiClient.getDashboardOverview(6)
  });
  // Polled on its own, lighter-weight cadence so recent activity stays fresh
  // without recomputing the full overview (rollups/trends) on every tick.
  const recentRunsQuery = useQuery({
    queryKey: ["dashboard-recent-runs"],
    queryFn: () => apiClient.getDashboardRecentRuns(8),
    refetchInterval: RECENT_RUNS_REFRESH_MS
  });

  if (overviewQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-ink-300">
        <Spinner /> Loading dashboard overview...
      </div>
    );
  }
  if (overviewQuery.isError) {
    return <p className="text-sm text-danger-400">Couldn't load the dashboard. Check that the API is running, then reload.</p>;
  }

  const data = overviewQuery.data;
  if (!data) {
    return <p className="text-sm text-ink-300">No dashboard data available.</p>;
  }
  const recentRuns = recentRunsQuery.data?.items ?? data.recent_runs ?? [];
  const latestRunId = data.headline_kpis?.latest_run_id;

  // First-run experience: nothing has ever executed, so guide instead of showing n/a walls.
  if (!latestRunId && recentRuns.length === 0) {
    return <FirstRunGuide />;
  }

  return (
    <section className="space-y-5">
      <PageHeader
        title="Dashboard"
        subtitle="What needs you first, then how the latest run compares with the one before."
      />
      {data.data_freshness?.degraded ? (
        <p role="status" className="rounded-md border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-sm text-warn-300">
          Some metrics are degraded: {data.data_freshness.notes?.join(", ") || "unknown source issue"}.
        </p>
      ) : null}

      <NeedsAttention />

      <TrendBaseline kpis={data.headline_kpis} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <ProjectHealthKpi />
        <KpiCard
          label="Failed"
         
          value={formatInt(data.headline_kpis.fail_count)}
          trend={data.trend_indicators.failed_count}
          lowerIsBetter
        />
        <KpiCard
          label="Passed tests"
         
          value={formatInt(data.headline_kpis.pass_count)}
          trend={{ direction: "unknown", delta_abs: null, delta_pct: null }}
        />
        <KpiCard
          label="Duration"
         
          value={formatDuration(data.headline_kpis.duration_ms)}
          trend={data.trend_indicators.duration}
          lowerIsBetter
        />
      </div>

      <DeliveryCard />

      <Card title="Recent runs" actions={<ReportLinks links={data.report_links} />}>
        {recentRuns.length === 0 ? (
          <p className="text-sm text-ink-400">No recent runs available.</p>
        ) : (
          <ul className="divide-y divide-white/5">
            {recentRuns.map((run) => (
              <li key={run.run_id} className="space-y-1.5 py-2.5 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-3">
                    <StatusPill status={run.status ?? "unknown"} returncode={run.returncode} />
                    <RunLabel run={run} linked relative href={run.run_detail_url} />
                  </div>
                  <div className="flex items-center gap-3 text-xs text-ink-400">
                    <span>{formatPercent(run.health_pct)} passed</span>
                    {run.compare_url ? (
                      <Link to={run.compare_url} className="-my-1 inline-block py-1 text-brand-300 hover:underline">
                        compare
                      </Link>
                    ) : null}
                  </div>
                </div>
                <HealthBar healthPct={run.health_pct} className="max-w-xs" />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </section>
  );
}

function FirstRunGuide() {
  return (
    <section className="space-y-6">
      <PageHeader title="Welcome to Testo" subtitle="Record a run and this page shows what broke, what's flaky and what got slower." />
      <div className="flex flex-col items-center gap-2 py-4">
        <MuscleShrug size={112} />
        <p className="text-sm text-ink-300">No runs yet. Any of these gets you one.</p>
      </div>
      <ol className="grid gap-4 md:grid-cols-3" data-testid="first-run-guide">
        <GuideStep
          step={1}
          title="Set up your project"
          body={
            <>
              In your repository, <code className="font-mono text-xs text-ink-100">testo init</code> finds your test
              runners, writes testosterone.yaml and a CI workflow, and records a first run. Then{" "}
              <code className="font-mono text-xs text-ink-100">testo run --cycle &lt;name&gt;</code> or Run on the{" "}
              <Link to="/cycles" className="text-brand-300 hover:underline">
                Cycles
              </Link>{" "}
              page.
            </>
          }
        />
        <GuideStep
          step={2}
          title="Or record the tests you already run"
          body={
            <>
              After any runner that writes results (pytest, Jest, Playwright, Go…):{" "}
              <code className="break-all font-mono text-xs text-ink-100">testo ingest</code> finds the files, or{" "}
              <code className="font-mono text-xs text-ink-100">pytest --testo</code>
            </>
          }
        />
        <GuideStep
          step={3}
          title="Just looking?"
          body={
            <>
              <Link to="/try" className="text-brand-300 hover:underline">
                Try a file
              </Link>
              : drop a JUnit XML file and see it read in the browser, with nothing uploaded.
            </>
          }
        />
      </ol>
    </section>
  );
}

function GuideStep({ step, title, body }: { step: number; title: string; body: React.ReactNode }) {
  return (
    <li>
      <Card className="h-full">
        <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-brand-600/20 font-mono text-sm font-bold text-brand-300">
          {step}
        </span>
        <h3 className="mt-2 text-sm font-semibold text-ink-100">{title}</h3>
        <p className="mt-1 text-sm text-ink-300">{body}</p>
      </Card>
    </li>
  );
}

// Trends are only meaningful within one cycle, so say which cycle and which run they compare against.
function TrendBaseline({ kpis }: { kpis: DashboardOverviewResponse["headline_kpis"] }) {
  if (!kpis.latest_run_id) {
    return null;
  }
  const cycle = kpis.cycle ?? "unnamed cycle";
  return (
    <p data-testid="trend-baseline" className="text-sm text-ink-300">
      Latest run: <strong className="text-ink-100">{cycle}</strong>.{" "}
      {kpis.baseline_run_id ? (
        <>
          Trends compare it with the{" "}
          <Link
            to={`/compare?current_run_id=${kpis.latest_run_id}&baseline_run_id=${kpis.baseline_run_id}`}
            className="text-brand-300 hover:text-brand-400 hover:underline"
          >
            previous {cycle} run
          </Link>
          .
        </>
      ) : (
        "No earlier run of this cycle in recent history, so there is no trend."
      )}
    </p>
  );
}

/**
 * The project's test health score, the same number the Project health page
 * leads with (one source: GET /api/v1/project-health), linking to that page.
 */
function ProjectHealthKpi() {
  const query = useQuery({ queryKey: ["project-health", ""], queryFn: () => apiClient.getProjectHealth() });
  const score = query.data?.score;
  return (
    <Link
      to="/health"
      className="glass-card group block p-4 transition-colors hover:border-brand-500/50"
      data-testid="project-health-kpi"
      aria-label={score == null ? "Project health: no score yet. Open project health" : `Project health: ${score} out of 100${query.data?.grade ? `, grade ${query.data.grade}` : ""}. Open project health`}
    >
      <strong className="text-xs font-medium text-ink-300">Project health</strong>
      <p className={`my-0.5 text-lg font-bold ${score == null ? "text-white" : TONE_TEXT[scoreTone(score)]}`}>
        {score == null ? "n/a" : `${score} / 100`}
        {query.data?.grade ? <span className="ml-1.5 text-sm font-semibold text-ink-300">{query.data.grade}</span> : null}
      </p>
      <p className="text-xs font-semibold text-brand-300 group-hover:underline">See what drives it →</p>
    </Link>
  );
}

function KpiCard({
  label,
  value,
  trend,
  lowerIsBetter = false
}: {
  label: string;
  value: string;
  trend: DashboardTrendIndicator;
  lowerIsBetter?: boolean;
}) {
  const semantic = trendSemantic(trend, lowerIsBetter);
  const trendColor =
    semantic.label === "improved"
      ? "text-success-400"
      : semantic.label === "regressed"
        ? "text-danger-400"
        : "text-ink-400";
  return (
    <article className="glass-card p-4">
      <div className="min-w-0">
        <strong className="text-xs font-medium text-ink-300">{label}</strong>
        <p className="my-0.5 text-lg font-bold text-white">{value}</p>
        {semantic.label === "unknown" ? null : (
          <p data-testid={`${label.toLowerCase().replace(/\s+/g, "-")}-trend`} className={`text-xs font-semibold ${trendColor}`}>
            {TREND_COPY[semantic.label]}
          </p>
        )}
      </div>
    </article>
  );
}

const TREND_COPY = { improved: "Better than last run", regressed: "Worse than last run", flat: "No change", unknown: "" };

export function trendSemantic(
  trend: DashboardTrendIndicator,
  lowerIsBetter: boolean
): { label: "improved" | "regressed" | "flat" | "unknown" } {
  if (trend.direction === "unknown") {
    return { label: "unknown" };
  }
  if (trend.direction === "flat") {
    return { label: "flat" };
  }
  if (lowerIsBetter) {
    return { label: trend.direction === "down" ? "improved" : "regressed" };
  }
  return { label: trend.direction === "up" ? "improved" : "regressed" };
}

/** The latest run's reports that exist; a report that isn't there is not worth a link. */
function ReportLinks({ links }: { links: DashboardOverviewResponse["report_links"] | undefined }) {
  const available = [
    { label: "Allure report", report: links?.allure },
    { label: "Behave report", report: links?.behave }
  ].filter(({ report }) => report?.state === "available" && report.url);
  if (available.length === 0) return null;
  return (
    <span className="flex gap-3 text-sm">
      {available.map(({ label, report }) => (
        <a
          key={label}
          href={report?.url ?? undefined}
          target="_blank"
          rel="noreferrer"
          className="-my-1 inline-block py-1 text-brand-300 hover:text-brand-400 hover:underline"
        >
          {label}
        </a>
      ))}
    </span>
  );
}

function formatInt(value: number | null): string {
  if (value == null) {
    return "n/a";
  }
  return `${Math.round(value)}`;
}
