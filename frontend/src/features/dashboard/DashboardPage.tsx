import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { DashboardOverviewResponse, DashboardTrendIndicator, apiClient } from "../../lib/api-client";
import { Card, HealthBar, Icons, PageHeader, RunLabel, ShowMoreButton, Spinner, StatusPill, useShowMore } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { formatDuration, formatPercent } from "../../lib/format";
import { DeliveryCard } from "./DeliveryCard";
import { LiveRunsCard } from "./LiveRunsCard";
import { NeedsAttention } from "./NeedsAttention";
import { Gauge, IrisArt, RunBars, SLICE_COLOR, type ResultSlice, type RunTone } from "./DashboardVisuals";
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
  const latestRun = recentRuns.find((run) => run.run_id === latestRunId) ?? recentRuns[0];

  // First-run experience: nothing has ever executed, so guide instead of showing n/a walls.
  if (!latestRunId && recentRuns.length === 0) {
    return <FirstRunGuide />;
  }

  return (
    <section className="space-y-5">
      <PageHeader
        title="Overview"
        subtitle="What needs you first, how the latest run did, and the two scores: your tests and your code."
      />
      {data.data_freshness?.degraded ? (
        <p role="status" className="rounded-md border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-sm text-warn-300">
          Some metrics are degraded: {data.data_freshness.notes?.join(", ") || "unknown source issue"}.
        </p>
      ) : null}

      <LiveRunsCard />

      <div className="grid gap-5 lg:grid-cols-12">
        <LatestRunHero data={data} latest={latestRun} />
        <div className="flex flex-col gap-5 lg:col-span-5">
          <ProjectHealthKpi />
          <CodeQualityKpi />
        </div>
      </div>

      <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
        <div className="flex min-w-0 flex-col gap-5 lg:w-5/12">
          <NeedsAttention />
          <DeliveryCard />
        </div>
        <div className="min-w-0 flex-1">
          <RecentRunsCard runs={recentRuns} reportLinks={data.report_links} />
        </div>
      </div>
    </section>
  );
}

type RecentRun = NonNullable<DashboardOverviewResponse["recent_runs"]>[number];

/** The latest three runs, with the rest one click away. */
function RecentRunsCard({ runs, reportLinks }: { runs: RecentRun[]; reportLinks: DashboardOverviewResponse["report_links"] }) {
  const rows = useShowMore(runs);
  return (
    <Card
      title="Recent runs"
      actions={<ReportLinks links={reportLinks} />}
      collapsible
      collapsedSummary={runs.length ? `${runs.length} recent` : undefined}
    >
      <RunBars
        runs={[...runs].reverse().map((run) => ({
          run_id: run.run_id,
          label: run.cycle ?? "run",
          health_pct: run.health_pct,
          tone: runTone(run)
        }))}
      />
      {runs.length === 0 ? (
        <p className="text-sm text-ink-400">No recent runs available.</p>
      ) : (
        <ul className="divide-fade mt-3" data-testid="recent-runs">
          {rows.visible.map((run) => (
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
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <ShowMoreButton total={rows.total} expanded={rows.expanded} onToggle={rows.setExpanded} />
        <Link to="/runs" className="text-sm text-brand-300 hover:underline">
          All runs
        </Link>
      </div>
    </Card>
  );
}

function FirstRunGuide() {
  return (
    <section className="space-y-6">
      <PageHeader title="Welcome to QASight" subtitle="Record a run and this page shows what broke, what's flaky and what got slower." />
      <LiveRunsCard />
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
              In your repository, <code className="font-mono text-xs text-ink-100">qasight init</code> finds your test
              runners, writes qasight.yaml and a CI workflow, and records a first run. Then{" "}
              <code className="font-mono text-xs text-ink-100">qasight run --cycle &lt;name&gt;</code> or Run on the{" "}
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
              <code className="break-all font-mono text-xs text-ink-100">qasight ingest</code> finds the files, or{" "}
              <code className="font-mono text-xs text-ink-100">pytest --qasight</code>
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
 * The project's test health score, the same number the Test health page
 * leads with (one source: GET /api/v1/project-health), linking to that page.
 */
function ProjectHealthKpi() {
  const query = useQuery({ queryKey: ["project-health", ""], queryFn: () => apiClient.getProjectHealth() });
  const score = query.data?.score;
  const tone = scoreTone(score);
  return (
    <Link
      to="/test-health"
      className={`glass-card group flex flex-1 flex-col p-5 ${CARD_HOVER}`}
      data-testid="project-health-kpi"
      aria-label={score == null ? "Test health: no score yet. Open test health" : `Test health: ${score} out of 100${query.data?.grade ? `, grade ${query.data.grade}` : ""}. Open test health`}
    >
      <strong className="text-base font-bold text-white">Test health</strong>
      <span className="text-sm text-ink-300">Your tests: passing, stable, fast. Scored out of 100</span>
      <div className="mt-2 flex flex-1 items-center justify-center">
        <Gauge value={score ?? null} color={GAUGE_COLOR[tone]}>
          <span className={`text-4xl font-bold tabular-nums ${score == null ? "text-white" : TONE_TEXT[tone]}`}>{score == null ? "n/a" : score}</span>
          <span className="text-xs text-ink-300">{query.data?.grade ? `Grade ${query.data.grade}` : "out of 100"}</span>
        </Gauge>
      </div>
      <p className="-mt-3 text-center text-sm font-semibold text-brand-300 group-hover:underline">See what drives it →</p>
    </Link>
  );
}

const CARD_HOVER =
  "transition-shadow duration-300 hover:shadow-[0_0_0_1px_rgb(52_211_153/0.45),0_12px_32px_-12px_rgb(16_185_129/0.5)]";

const GRADE_TONE: Record<string, keyof typeof TONE_TEXT> = { A: "success", B: "success", C: "warn", D: "danger", E: "danger" };

/**
 * The code's grade from the latest scored version (GET /api/v1/scorecard), the second score
 * beside Test health, so the two are never mistaken for each other.
 */
function CodeQualityKpi() {
  const query = useQuery({ queryKey: ["scorecard", "", ""], queryFn: () => apiClient.getScorecard() });
  const card = query.data;
  const scored = card?.status === "available" && card.score != null;
  const tone = scored ? (GRADE_TONE[card.grade ?? ""] ?? "neutral") : "neutral";
  return (
    <Link
      to="/code-quality"
      className={`glass-card group flex items-center justify-between gap-4 p-5 ${CARD_HOVER}`}
      data-testid="code-quality-kpi"
      aria-label={
        scored
          ? `Code quality of ${card.version}: grade ${card.grade}, ${card.score} out of 100. Open code quality`
          : "Code quality: not scanned yet. Open code quality"
      }
    >
      <div className="min-w-0">
        <strong className="block text-base font-bold text-white">Code quality</strong>
        <span className="block text-sm text-ink-300">
          {scored ? `Your code in ${card.version}: linters, security, API and load tools` : "Your code, graded per release. Not scanned yet"}
        </span>
      </div>
      <span className={`shrink-0 text-right text-3xl font-bold tabular-nums ${scored ? TONE_TEXT[tone] : "text-ink-400"}`}>
        {scored ? (
          <>
            {card.grade} <span className="text-white">{card.score}</span>
          </>
        ) : (
          "n/a"
        )}
      </span>
    </Link>
  );
}

const GAUGE_COLOR = { success: "#34d399", warn: "#fbbf24", danger: "#f87171", neutral: "#56618f" } as const;

function runTone(run: RecentRun | undefined): RunTone {
  if (!run) return "neutral";
  const status = (run.status ?? "").toLowerCase();
  if (run.returncode === 0 && !status.includes("fail")) return "success";
  return "danger";
}

/**
 * The dashboard's opening card: the latest run's result in big type, with
 * QASight's eye in the run's colour beside it and three small numbers below.
 */
function LatestRunHero({ data, latest }: { data: DashboardOverviewResponse; latest: RecentRun | undefined }) {
  const kpis = data.headline_kpis;
  const tone = runTone(latest);
  const failed = kpis.fail_count ?? 0;
  // Per-status counts for the iris pie; the dashboard overview only carries passed and failed.
  const counts = useQuery({
    queryKey: ["latest-run-counts", kpis.latest_run_id],
    queryFn: () => apiClient.listRuns(kpis.cycle ?? undefined, 5),
    enabled: Boolean(kpis.latest_run_id)
  });
  const row = counts.data?.items.find((r) => r.run_id === kpis.latest_run_id);
  const slices: ResultSlice[] | undefined = row
    ? [
        { key: "passed", label: "passed", count: row.passed ?? 0 },
        { key: "failed", label: "failed", count: row.failed ?? 0 },
        { key: "broken", label: "broken", count: row.broken ?? 0 },
        { key: "skipped", label: "skipped", count: row.skipped ?? 0 }
      ]
    : undefined;
  const headline =
    latest?.health_pct != null ? `${formatPercent(latest.health_pct)} passed` : tone === "success" ? "Passed" : tone === "danger" ? "Failed" : "No result yet";
  return (
    <article className="glass-card relative overflow-hidden p-5 sm:p-6 lg:col-span-7" data-testid="latest-run-hero">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            tone === "danger"
              ? "radial-gradient(circle at 85% 40%, rgb(248 113 113 / 0.18), transparent 55%)"
              : tone === "success"
                ? "radial-gradient(circle at 85% 40%, rgb(52 211 153 / 0.16), transparent 55%)"
                : "radial-gradient(circle at 85% 40%, rgb(45 212 191 / 0.18), transparent 55%)"
        }}
      />
      <IrisArt tone={tone} slices={slices} className="pointer-events-none absolute -right-24 -top-20 h-[230px] w-[260px] opacity-35 sm:-right-6 sm:-top-6 sm:h-[300px] sm:w-[340px] sm:opacity-90 lg:-right-24 lg:-top-20 lg:h-[230px] lg:w-[260px] lg:opacity-50 wide:-right-6 wide:-top-6 wide:h-[300px] wide:w-[340px] wide:opacity-90" />
      <div className="relative max-w-md">
        <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-ink-300">
          Latest run
          {latest ? <StatusPill status={latest.status ?? "unknown"} returncode={latest.returncode} /> : null}
        </p>
        <p className="mt-2 text-3xl font-bold text-white sm:text-4xl">{headline}</p>
        <div className="mt-2 max-w-sm">
          <TrendBaseline kpis={kpis} />
        </div>
        {kpis.latest_run_id ? (
          <Link to={`/runs/${kpis.latest_run_id}`} className="mt-3 inline-flex items-center gap-1 text-sm font-semibold text-white hover:text-brand-300">
            Open the run <span aria-hidden>→</span>
          </Link>
        ) : null}
      </div>
      <div className="relative mt-6 grid grid-cols-3 gap-3 sm:max-w-md">
        <MiniStat label="Failed" icon={<Icons.CrossIcon />} alarm={failed > 0} value={formatInt(kpis.fail_count)} trend={data.trend_indicators.failed_count} lowerIsBetter />
        <MiniStat label="Passed tests" icon={<Icons.CheckIcon />} value={formatInt(kpis.pass_count)} trend={{ direction: "unknown", delta_abs: null, delta_pct: null }} />
        <MiniStat label="Duration" icon={<Icons.ClockIcon />} value={formatDuration(kpis.duration_ms)} trend={data.trend_indicators.duration} lowerIsBetter />
      </div>
      {slices && slices.some((s) => s.count > 0) ? (
        <ul className="relative mt-4 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-300 sm:justify-end" data-testid="iris-legend">
          {slices
            .filter((s) => s.count > 0)
            .map((s) => (
              <li key={s.key} className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full" style={{ background: SLICE_COLOR[s.key] }} aria-hidden />
                <span className="font-semibold tabular-nums text-white">{s.count}</span> {s.label}
              </li>
            ))}
        </ul>
      ) : null}
    </article>
  );
}

function MiniStat({
  label,
  value,
  trend,
  icon,
  alarm = false,
  lowerIsBetter = false
}: {
  label: string;
  value: string;
  trend: DashboardTrendIndicator;
  /** Shown in a small tile before the label; red instead of blue when `alarm` is set. */
  icon: React.ReactNode;
  alarm?: boolean;
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
    <div className="min-w-0 rounded-2xl bg-ink-950/70 p-3 backdrop-blur-md">
      <p className="flex items-center gap-2 text-xs font-medium text-ink-300">
        <span
          aria-hidden
          className={`hidden h-6 w-6 shrink-0 items-center justify-center rounded-lg sm:flex [&_svg]:h-3.5 [&_svg]:w-3.5 ${
            alarm ? "bg-gradient-to-br from-danger-400 to-danger-600 text-white" : "brand-tile"
          }`}
        >
          {icon}
        </span>
        <span className="truncate">{label}</span>
      </p>
      <p className="mt-1.5 text-lg font-bold tabular-nums text-white">{value}</p>
      {semantic.label === "unknown" ? null : (
        <p data-testid={`${label.toLowerCase().replace(/\s+/g, "-")}-trend`} className={`text-[11px] font-semibold ${trendColor}`}>
          {TREND_COPY[semantic.label]}
        </p>
      )}
    </div>
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
