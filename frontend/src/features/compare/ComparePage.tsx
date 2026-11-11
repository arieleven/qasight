import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  CaseChangeKind,
  DeltaCaseChange,
  DeltaCaseChangesResponse,
  DeltaClassification,
  DeltaComparisonResponse,
  DeltaMetricNode,
  RunListItem,
  apiClient
} from "../../lib/api-client";
import { countLabel, formatRunLabel, runCycle, shortRunId } from "../../lib/format";
import { Badge, Card, PageHeader, RunLabel, StackedBar, type StackedBarSegment } from "../../components/ui";

const CASE_KIND_TONE: Record<CaseChangeKind, string> = {
  regression: "text-danger-300",
  fix: "text-success-300",
  added: "text-brand-300",
  removed: "text-warn-300",
  status_change: "text-ink-300"
};

const CASE_KIND_LABELS: CaseChangeKind[] = ["regression", "fix", "added", "removed", "status_change"];

const CASE_KIND_TEXT: Record<CaseChangeKind, string> = {
  regression: "Regression",
  fix: "Fix",
  added: "Added",
  removed: "Removed",
  status_change: "Status change"
};

const CASE_KIND_COUNT_TONE: Record<CaseChangeKind, "danger" | "success" | "neutral"> = {
  regression: "danger",
  fix: "success",
  added: "neutral",
  removed: "neutral",
  status_change: "neutral"
};

/** Singular noun for "N regressions", "N fixes", "N added"; countLabel adds the s. */
const CASE_KIND_COUNT_WORD: Record<CaseChangeKind, string> = {
  regression: "regression",
  fix: "fix",
  added: "added test",
  removed: "removed test",
  status_change: "status change"
};

const CLASSIFICATION_TONE: Record<DeltaClassification, string> = {
  regression: "text-danger-300",
  improvement: "text-success-300",
  neutral: "text-ink-300",
  unknown: "text-ink-400"
};

const CLASSIFICATION_BADGE: Record<DeltaClassification, { tone: "danger" | "success" | "neutral"; label: string }> = {
  regression: { tone: "danger", label: "Regression" },
  improvement: { tone: "success", label: "Improvement" },
  neutral: { tone: "neutral", label: "No change" },
  unknown: { tone: "neutral", label: "Unknown" }
};

type ReliabilityMetrics = DeltaComparisonResponse["metrics"]["reliability"];
type PerformanceMetrics = DeltaComparisonResponse["metrics"]["performance"];

const RELIABILITY_ORDER: Array<{ key: keyof ReliabilityMetrics; label: string }> = [
  { key: "total_tests", label: "Total tests" },
  { key: "passed", label: "Passed" },
  { key: "failed", label: "Failed" },
  { key: "broken", label: "Broken" },
  { key: "skipped", label: "Skipped" },
  { key: "health_pct", label: "Pass rate" }
];

const PERFORMANCE_ORDER: Array<{ key: keyof PerformanceMetrics; label: string }> = [
  { key: "wall_duration_ms", label: "Wall duration" },
  { key: "metrics_duration_ms", label: "Test time (sum)" },
  { key: "avg_case_ms", label: "Avg per test" }
];

const ALL_CYCLES = "";

const selectClass = "w-full min-w-0 max-w-full rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 focus:border-brand-500 focus:outline-none text-sm text-ink-100";
const labelClass = "grid min-w-0 gap-1 text-xs font-medium text-ink-300";

/** The newest run and the run of its cycle before it: what someone opening Compare usually wants. */
function latestPair(runs: RunListItem[]): { cycle: string; current: string; baseline: string } | null {
  const [latest] = [...runs].sort((a, b) => b.created_at - a.created_at);
  if (!latest) return null;
  const cycle = runCycle(latest);
  const previous = runs
    .filter((run) => runCycle(run) === cycle && run.run_id !== latest.run_id && run.created_at <= latest.created_at)
    .sort((a, b) => b.created_at - a.created_at)[0];
  return previous ? { cycle, current: latest.run_id, baseline: previous.run_id } : null;
}

export function ComparePage() {
  const [searchParams] = useSearchParams();
  const [pickedCurrent, setPickedCurrent] = useState(searchParams.get("current_run_id") ?? "");
  const [pickedBaseline, setPickedBaseline] = useState(searchParams.get("baseline_run_id") ?? "");
  const [pickedCycle, setPickedCycle] = useState(ALL_CYCLES);
  // Until someone picks, a bare /compare shows the latest run against the one before it.
  const [pristine, setPristine] = useState(!pickedCurrent && !pickedBaseline);
  const [kindFilter, setKindFilter] = useState<CaseChangeKind | "all">("all");
  const changesRef = useRef<HTMLElement>(null);
  const runsQuery = useQuery({
    queryKey: ["runs"],
    // Enough history to pick an older baseline than the newest page.
    queryFn: () => apiClient.listRuns(undefined, 200)
  });

  const options = useMemo(() => runsQuery.data?.items ?? [], [runsQuery.data?.items]);
  const auto = useMemo(() => (pristine ? latestPair(options) : null), [pristine, options]);
  const currentRunId = auto?.current ?? pickedCurrent;
  const baselineRunId = auto?.baseline ?? pickedBaseline;
  const cycleFilter = auto?.cycle ?? pickedCycle;

  const compareQuery = useQuery({
    queryKey: ["delta-comparison", currentRunId, baselineRunId],
    queryFn: () => apiClient.getDeltaComparison(currentRunId, baselineRunId),
    enabled: Boolean(currentRunId && baselineRunId && currentRunId !== baselineRunId)
  });

  // The per-test diff, fetched with the comparison: the summary counts it and the list under the summary shows it.
  const casesQuery = useQuery({
    queryKey: ["delta-case-changes", currentRunId, baselineRunId],
    queryFn: () => apiClient.getDeltaCaseChanges(currentRunId, baselineRunId),
    enabled: Boolean(compareQuery.data)
  });
  const changes = useMemo(() => casesQuery.data?.changes ?? [], [casesQuery.data?.changes]);
  const changeCounts = useMemo(() => {
    const byKind = new Map<CaseChangeKind, number>();
    for (const change of changes) byKind.set(change.kind, (byKind.get(change.kind) ?? 0) + 1);
    return byKind;
  }, [changes]);

  const runsById = useMemo(() => new Map(options.map((run) => [run.run_id, run])), [options]);
  const cycles = useMemo(() => [...new Set(options.map(runCycle))], [options]);
  const visibleRuns = useMemo(
    () => (cycleFilter === ALL_CYCLES ? options : options.filter((run) => runCycle(run) === cycleFilter)),
    [options, cycleFilter]
  );

  if (runsQuery.isLoading) {
    return <CompareState><p className="text-sm text-ink-300">Loading runs…</p></CompareState>;
  }
  if (runsQuery.isError) {
    return (
      <CompareState>
        <p className="text-sm text-danger-400">Couldn't load runs to compare. Check that the API is running, then reload.</p>
      </CompareState>
    );
  }
  if (options.length < 2) {
    return (
      <CompareState>
        <p className="text-sm text-ink-300">At least two completed runs are required for comparison.</p>
      </CompareState>
    );
  }

  // The first pick keeps what was shown automatically, then changes one thing.
  const pick = (next: { current?: string; baseline?: string; cycle?: string }) => {
    setPristine(false);
    setPickedCurrent(next.current ?? currentRunId);
    setPickedBaseline(next.baseline ?? baselineRunId);
    setPickedCycle(next.cycle ?? cycleFilter);
  };

  // Narrowing to one cycle compares its latest run (current) with the run before it (baseline).
  const selectCycle = (cycle: string) => {
    if (cycle === ALL_CYCLES) {
      pick({ cycle });
      return;
    }
    const [latest, previous] = options
      .filter((run) => runCycle(run) === cycle)
      .sort((a, b) => b.created_at - a.created_at);
    pick({ cycle, current: latest?.run_id ?? "", baseline: previous?.run_id ?? "" });
  };

  // A count in the summary is a question ("which ones?"); the answer is the filtered list right under it.
  const showChanges = (kind: CaseChangeKind) => {
    setKindFilter(kind);
    changesRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };

  const comparisonReady = Boolean(currentRunId && baselineRunId && currentRunId !== baselineRunId);
  const payload = compareQuery.data;
  const currentRun = runsById.get(currentRunId);
  const baselineRun = runsById.get(baselineRunId);
  const crossCycle = Boolean(currentRun && baselineRun && runCycle(currentRun) !== runCycle(baselineRun));

  return (
    <section className="space-y-4">
      <PageHeader title="Compare runs" subtitle={COMPARE_SUBTITLE} />

      <div className="grid gap-4 glass-card p-4 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)_minmax(0,1fr)]">
        <label className={labelClass}>
          Cycle
          <select
            aria-label="Cycle"
            value={cycleFilter}
            onChange={(event) => selectCycle(event.target.value)}
            className={selectClass}
          >
            <option value={ALL_CYCLES}>All cycles</option>
            {cycles.map((cycle) => (
              <option key={cycle} value={cycle}>
                {cycle}
              </option>
            ))}
          </select>
        </label>
        <RunPicker label="Baseline run" value={baselineRunId} onChange={(id) => pick({ baseline: id })} runs={visibleRuns} />
        <RunPicker label="Current run" value={currentRunId} onChange={(id) => pick({ current: id })} runs={visibleRuns} />
      </div>

      {crossCycle && (
        <p role="status" className="rounded-md border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-sm text-warn-300">
          These runs come from different cycles ({baselineRun && runCycle(baselineRun)} and{" "}
          {currentRun && runCycle(currentRun)}), so the
          differences below reflect different test suites rather than regressions.
        </p>
      )}

      {!comparisonReady && <p className="text-sm text-ink-400">Select two different runs to start comparison.</p>}
      {comparisonReady && compareQuery.isLoading && <p className="text-sm text-ink-300">Comparing runs…</p>}
      {comparisonReady && compareQuery.isError && <p className="text-sm text-danger-400">Couldn't compare these runs. One of them may have been cleaned up.</p>}
      {comparisonReady && payload && (
        <>
          <Card>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="min-w-0">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-400">Baseline</p>
                <RunRef runId={payload.comparison.baseline_run_id} run={baselineRun} />
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium uppercase tracking-wide text-ink-400">Current</p>
                <RunRef runId={payload.comparison.current_run_id} run={currentRun} />
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-1.5" data-testid="status-summary">
              <span className="mr-1 text-xs text-ink-400">Metrics</span>
              <Badge tone={payload.status_summary.regressions.length > 0 ? "danger" : "neutral"}>
                {countLabel(payload.status_summary.regressions.length, "metric")} worse
              </Badge>
              <Badge tone={payload.status_summary.improvements.length > 0 ? "success" : "neutral"}>
                {payload.status_summary.improvements.length} better
              </Badge>
              <Badge>{payload.status_summary.unchanged.length} unchanged</Badge>
              {payload.status_summary.unknown.length > 0 ? (
                <Badge>{payload.status_summary.unknown.length} not comparable</Badge>
              ) : null}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5" data-testid="test-change-counts">
              <span className="mr-1 text-xs text-ink-400">Tests</span>
              {casesQuery.isLoading ? <span className="text-xs text-ink-400">counting…</span> : null}
              {casesQuery.isError ? <span className="text-xs text-ink-400">per-test changes unavailable</span> : null}
              {casesQuery.data && changes.length === 0 ? (
                <span className="text-xs text-ink-300">same outcome for every test</span>
              ) : null}
              {CASE_KIND_LABELS.filter((kind) => (changeCounts.get(kind) ?? 0) > 0).map((kind) => (
                <CountButton
                  key={kind}
                  tone={CASE_KIND_COUNT_TONE[kind]}
                  label={countLabel(changeCounts.get(kind) ?? 0, CASE_KIND_COUNT_WORD[kind])}
                  onClick={() => showChanges(kind)}
                />
              ))}
            </div>
          </Card>

          <TestLevelChanges
            ref={changesRef}
            query={casesQuery}
            changes={changes}
            counts={changeCounts}
            cycle={currentRun ? runCycle(currentRun) : null}
            kindFilter={kindFilter}
            onKindFilter={setKindFilter}
          />

          <Card title="Outcome mix">
            <div className="space-y-3">
              <div>
                <p className="mb-1 text-xs font-medium text-ink-400">Baseline · {runText(payload.comparison.baseline_run_id, baselineRun)}</p>
                <StackedBar segments={outcomeSegments(payload.metrics.reliability, "baseline_value")} />
              </div>
              <div>
                <p className="mb-1 text-xs font-medium text-ink-400">Current · {runText(payload.comparison.current_run_id, currentRun)}</p>
                <StackedBar segments={outcomeSegments(payload.metrics.reliability, "current_value")} />
              </div>
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <MetricTable title="Reliability" rows={RELIABILITY_ORDER} metrics={payload.metrics.reliability} />
            <MetricTable title="Performance" rows={performanceRows(payload.metrics.performance)} metrics={payload.metrics.performance} />
          </div>

          {(payload.stage_deltas ?? []).length > 0 && (
            <Card title="Pass rate by stage">
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm text-ink-300">
                  <thead>
                    <tr className="border-b border-white/10 text-[10px] uppercase tracking-wider text-ink-400">
                      <th className="pb-1 pr-2 font-medium">Stage</th>
                      <th className="pb-1 pr-2 text-right font-medium">Baseline</th>
                      <th className="pb-1 pr-2 text-right font-medium">Current</th>
                      <th className="pb-1 text-right font-medium">Change</th>
                    </tr>
                  </thead>
                  <tbody>
                    {payload.stage_deltas.map((stage) => (
                      <tr key={stage.stage_name} className="border-t border-white/5">
                        <td className="py-1.5 pr-2 text-ink-100">
                          {stage.stage_name}
                          {stage.framework ? <span className="ml-2 text-xs text-ink-400">{stage.framework}</span> : null}
                        </td>
                        <td className="py-1.5 pr-2 whitespace-nowrap text-right font-mono text-xs">
                          {stage.baseline_health_pct != null ? `${stage.baseline_health_pct.toFixed(1)}%` : "n/a"}
                        </td>
                        <td className="py-1.5 pr-2 whitespace-nowrap text-right font-mono text-xs">
                          {stage.current_health_pct != null ? `${stage.current_health_pct.toFixed(1)}%` : "n/a"}
                        </td>
                        <td className={`py-1.5 whitespace-nowrap text-right font-mono text-xs ${CLASSIFICATION_TONE[stage.classification]}`}>
                          {stage.health_pct_delta != null ? formatSignedDelta(stage.health_pct_delta, "pct") : "n/a"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          <Card title="Highlights">
            {payload.highlights.length === 0 ? (
              <p className="text-sm text-ink-400">No major changes detected.</p>
            ) : (
              <ul className="list-disc space-y-1 pl-5 text-sm text-ink-300">
                {payload.highlights.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </section>
  );
}

/**
 * "Test time (sum)" is the wall duration again for a single-stage run (every stage sums to the run):
 * a row that repeats the one above it is dropped.
 */
function performanceRows(metrics: PerformanceMetrics): typeof PERFORMANCE_ORDER {
  const wall = metrics.wall_duration_ms;
  const sum = metrics.metrics_duration_ms;
  const duplicate = sum.baseline_value === wall.baseline_value && sum.current_value === wall.current_value;
  return duplicate ? PERFORMANCE_ORDER.filter((row) => row.key !== "metrics_duration_ms") : PERFORMANCE_ORDER;
}

const COUNT_TONE = {
  danger: "border-danger-500/40 bg-danger-500/15 text-danger-300 hover:bg-danger-500/25",
  success: "border-success-500/40 bg-success-500/15 text-success-300 hover:bg-success-500/25",
  neutral: "border-ink-700 bg-ink-800/60 text-ink-300 hover:bg-ink-800"
};

/** A summary count that opens the matching test-level list. */
function CountButton({ tone, label, onClick }: { tone: keyof typeof COUNT_TONE; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${COUNT_TONE[tone]}`}
    >
      {label}
    </button>
  );
}

/** Run select grouped by cycle; each option reads "cycle · time · short id". */
function RunPicker({
  label,
  value,
  onChange,
  runs
}: {
  label: string;
  value: string;
  onChange: (runId: string) => void;
  runs: RunListItem[];
}) {
  const groups = useMemo(() => {
    const byCycle = new Map<string, RunListItem[]>();
    for (const run of runs) {
      const cycle = runCycle(run);
      const group = byCycle.get(cycle);
      if (group) {
        group.push(run);
      } else {
        byCycle.set(cycle, [run]);
      }
    }
    return [...byCycle.entries()];
  }, [runs]);

  return (
    <label className={labelClass}>
      {label}
      <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} className={selectClass}>
        <option value="">Select run</option>
        {groups.map(([cycle, cycleRuns]) => (
          <optgroup key={cycle} label={cycle}>
            {cycleRuns.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {[formatRunLabel(run), run.branch, shortRunId(run.run_id)].filter(Boolean).join(" · ")}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}

function RunRef({ runId, run }: { runId: string; run: RunListItem | undefined }) {
  return run ? <RunLabel run={run} linked /> : <span className="font-mono text-sm text-ink-100">{runId}</span>;
}

function runText(runId: string, run: RunListItem | undefined): string {
  return run ? formatRunLabel(run) : shortRunId(runId);
}

function MetricTable<K extends string>({
  title,
  rows,
  metrics
}: {
  title: string;
  rows: Array<{ key: K; label: string }>;
  metrics: Record<K, DeltaMetricNode>;
}) {
  return (
    <Card title={title} className="min-w-0">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm text-ink-300">
          <thead>
            <tr className="border-b border-white/10 text-[10px] uppercase tracking-wider text-ink-400">
              <th className="pb-1 pr-2 font-medium">Metric</th>
              <th className="pb-1 pr-2 text-right font-medium">Baseline</th>
              <th className="pb-1 pr-2 text-right font-medium">Current</th>
              <th className="pb-1 pr-2 text-right font-medium">Change</th>
              <th className="hidden pb-1 font-medium sm:table-cell">State</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ key, label }) => {
              const metric = metrics[key];
              const badge = CLASSIFICATION_BADGE[metric.classification];
              return (
                <tr key={key} className="border-t border-white/5" data-testid={`metric-${key}`}>
                  <td className="py-1.5 pr-2 text-ink-100">{label}</td>
                  <td className="py-1.5 pr-2 whitespace-nowrap text-right font-mono text-xs">{formatMetricValue(metric.baseline_value, metric.unit)}</td>
                  <td className="py-1.5 pr-2 whitespace-nowrap text-right font-mono text-xs">{formatMetricValue(metric.current_value, metric.unit)}</td>
                  <td className={`py-1.5 pr-2 whitespace-nowrap text-right font-mono text-xs ${CLASSIFICATION_TONE[metric.classification]}`}>
                    <MetricChange metric={metric} />
                  </td>
                  <td className="hidden py-1.5 sm:table-cell">
                    {metric.reason === "within_noise" ? (
                      <Badge tone="neutral" title="Under 5% (and under the metric's minimum in ms): normal timing jitter">
                        Within noise
                      </Badge>
                    ) : (
                      <Badge tone={badge.tone} title={metric.reason ?? undefined}>
                        {badge.label}
                      </Badge>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function MetricChange({ metric }: { metric: DeltaMetricNode }) {
  if (metric.absolute_delta == null) {
    return <span title={metric.reason ?? undefined}>n/a</span>;
  }
  if (metric.absolute_delta === 0) {
    return <span>—</span>;
  }
  // Relative change of a percentage is confusing next to percentage points, so skip it for health.
  const relative =
    metric.relative_delta_pct != null && metric.unit !== "pct" ? ` (${formatSigned(metric.relative_delta_pct, 1)}%)` : "";
  return (
    <span className="whitespace-nowrap">
      <span aria-hidden="true">{metric.absolute_delta > 0 ? "▲ " : "▼ "}</span>
      {formatSignedDelta(metric.absolute_delta, metric.unit)}
      <span className="text-ink-400">{relative}</span>
    </span>
  );
}

function formatMetricValue(value: number | null, unit: DeltaMetricNode["unit"]): string {
  if (value == null) {
    return "n/a";
  }
  if (unit === "tests") {
    return `${Math.round(value)}`;
  }
  if (unit === "pct") {
    return `${value.toFixed(1)}%`;
  }
  return formatDuration(value);
}

function formatSignedDelta(value: number, unit: DeltaMetricNode["unit"]): string {
  if (unit === "tests") {
    return formatSigned(value, 0);
  }
  if (unit === "pct") {
    return `${formatSigned(value, 1)} pp`;
  }
  const magnitude = formatDuration(Math.abs(value));
  return `${/[1-9]/.test(magnitude) ? (value > 0 ? "+" : "−") : ""}${magnitude}`;
}

/** Signed number; values that round to zero get no sign, so "no change" never reads as "−0.0". */
function formatSigned(value: number, digits: number): string {
  const magnitude = Math.abs(value).toFixed(digits);
  if (Number(magnitude) === 0) {
    return magnitude;
  }
  return `${value > 0 ? "+" : "−"}${magnitude}`;
}

function formatDuration(ms: number): string {
  if (ms >= 999.5) {
    return `${(ms / 1000).toFixed(2)} s`;
  }
  return ms < 99.95 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}

function outcomeSegments(reliability: ReliabilityMetrics, field: "current_value" | "baseline_value"): StackedBarSegment[] {
  return [
    { label: "Passed", value: reliability.passed[field] ?? 0, colorClass: "bg-success-400" },
    { label: "Failed", value: reliability.failed[field] ?? 0, colorClass: "bg-danger-400" },
    { label: "Broken", value: reliability.broken[field] ?? 0, colorClass: "bg-danger-300" },
    { label: "Skipped", value: reliability.skipped[field] ?? 0, colorClass: "bg-warn-400" }
  ];
}

interface TestLevelChangesProps {
  query: { isLoading: boolean; isError: boolean; data: DeltaCaseChangesResponse | undefined };
  changes: DeltaCaseChange[];
  counts: Map<CaseChangeKind, number>;
  /** The current run's cycle, so each test can link to its history page. */
  cycle: string | null;
  kindFilter: CaseChangeKind | "all";
  onKindFilter: (kind: CaseChangeKind | "all") => void;
  ref?: React.Ref<HTMLElement>;
}

/** Which tests changed between the two runs: open by default, right under the summary that counts them. */
function TestLevelChanges({ query: casesQuery, changes, counts, cycle, kindFilter, onKindFilter, ref }: TestLevelChangesProps) {
  const [expanded, setExpanded] = useState(true);

  const filtered = useMemo(
    () => (kindFilter === "all" ? changes : changes.filter((c) => c.kind === kindFilter)),
    [changes, kindFilter]
  );
  const grouped = useMemo(() => {
    const byGroup = new Map<string, typeof filtered>();
    for (const change of filtered) {
      const list = byGroup.get(change.group) ?? [];
      list.push(change);
      byGroup.set(change.group, list);
    }
    return [...byGroup.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  const testLink = (key: string) =>
    cycle ? `/cycles/${encodeURIComponent(cycle)}/test?${new URLSearchParams({ key }).toString()}` : null;

  return (
    <section ref={ref} className="glass-card scroll-mt-4 p-4">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 text-sm font-semibold text-ink-100"
        aria-expanded={expanded}
      >
        <span className={`transition-transform ${expanded ? "rotate-90" : ""}`}>&#9656;</span>
        Test-level changes
        {casesQuery.data ? <span className="font-normal text-ink-400">({changes.length})</span> : null}
      </button>
      <p className="mt-1 text-xs text-ink-400">
        Per test: which ones regressed, were fixed, added or removed between the two runs, same as <code>qasight diff</code>.
      </p>

      {expanded && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-2">
            <FilterButton label="All" count={changes.length} active={kindFilter === "all"} onClick={() => onKindFilter("all")} />
            {CASE_KIND_LABELS.map((kind) => (
              <FilterButton
                key={kind}
                label={CASE_KIND_TEXT[kind]}
                count={counts.get(kind) ?? 0}
                active={kindFilter === kind}
                onClick={() => onKindFilter(kind)}
              />
            ))}
          </div>

          {casesQuery.isLoading && <p className="text-sm text-ink-300">Loading test-level changes...</p>}
          {casesQuery.isError && <p className="text-sm text-danger-400">Couldn't load the test-level changes.</p>}
          {casesQuery.data && changes.length === 0 && (
            <p className="text-sm text-ink-400">Every test has the same outcome in both runs.</p>
          )}
          {casesQuery.data && changes.length > 0 && filtered.length === 0 && (
            <p className="text-sm text-ink-400">No matching test changes.</p>
          )}
          {grouped.map(([group, groupChanges]) => (
            <div key={group}>
              <p className="mb-1 font-mono text-xs text-ink-400">{group}</p>
              <ul className="space-y-1 border-l border-white/5 pl-3">
                {groupChanges.map((change) => {
                  const href = testLink(change.key);
                  return (
                    <li key={change.key} className="flex flex-wrap items-center gap-2 text-sm">
                      <span className={`text-xs font-semibold ${CASE_KIND_TONE[change.kind]}`}>{CASE_KIND_TEXT[change.kind]}</span>
                      {href ? (
                        <Link to={href} className="text-ink-100 hover:text-brand-300 hover:underline">
                          {change.name}
                        </Link>
                      ) : (
                        <span className="text-ink-100">{change.name}</span>
                      )}
                      <span className="font-mono text-xs text-ink-400">
                        {change.baseline_status ?? "—"} → {change.current_status ?? "—"}
                        {change.duration_delta_ms != null ? ` (${change.duration_delta_ms >= 0 ? "+" : ""}${change.duration_delta_ms}ms)` : ""}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function FilterButton({ label, count, active, onClick }: { label: string; count: number; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`rounded-full border px-3 py-1 text-xs font-medium ${
        active ? "border-brand-400 bg-brand-500/20 text-brand-200" : "border-ink-700 text-ink-300 hover:border-ink-600"
      }`}
    >
      {label} <span className="font-mono opacity-70">{count}</span>
    </button>
  );
}

const COMPARE_SUBTITLE = "Pick a baseline and a current run to see what regressed and what improved.";

/** The page's heading around a state with nothing to compare yet, so the page always has its h1. */
function CompareState({ children }: { children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <PageHeader title="Compare runs" subtitle={COMPARE_SUBTITLE} />
      {children}
    </section>
  );
}
