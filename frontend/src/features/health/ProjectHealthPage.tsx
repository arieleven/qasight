import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient, type HealthFindingItem, type ProjectHealthResponse } from "../../lib/api-client";
import { Badge, Card, EmptyState, PageHeader, Spinner } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { countLabel, formatRunTime, testPath } from "../../lib/format";
import { AiReviewCard } from "./AiReviewCard";
import { ScoreRing, TONE_BAR, TONE_TEXT, scoreTone } from "./score";

function Signals({ signals }: { signals: ProjectHealthResponse["signals"] }) {
  return (
    <ul className="space-y-3">
      {signals.map((signal) => {
        const tone = scoreTone(signal.score);
        return (
          <li key={signal.key}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="text-sm font-semibold text-ink-100">
                {signal.name} <span className="text-xs font-normal text-ink-400">· weight {signal.weight}%</span>
              </span>
              <span className={`text-sm font-semibold ${TONE_TEXT[tone]}`}>{signal.score ?? "No data"}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-800" aria-hidden>
              <div className={`h-full rounded-full ${TONE_BAR[tone]}`} style={{ width: `${signal.score ?? 0}%` }} />
            </div>
            <p className="mt-1 text-xs text-ink-300">{signal.value}</p>
            <p className="text-xs text-ink-400">{signal.how}</p>
          </li>
        );
      })}
    </ul>
  );
}

const FINDING_BADGE: Record<HealthFindingItem["kind"], { label: string; tone: "danger" | "warn" | "neutral" }> = {
  red_cycle: { label: "Red", tone: "danger" },
  flaky: { label: "Flaky", tone: "warn" },
  slower: { label: "Slower", tone: "warn" },
  quarantined: { label: "Quarantined", tone: "neutral" },
  skipped: { label: "Skipped", tone: "neutral" }
};

function findingLink(finding: HealthFindingItem): string | null {
  if (finding.test_key) return testPath(finding.cycle, finding.test_key);
  if (finding.run_id) return `/runs/${finding.run_id}`;
  return null;
}

function Findings({ findings }: { findings: HealthFindingItem[] }) {
  if (findings.length === 0) {
    return <p className="text-sm text-success-300">Nothing to fix: no red cycles, flaky, slower, skipped or quarantined tests.</p>;
  }
  return (
    <ul className="divide-y divide-white/5">
      {findings.map((finding, index) => {
        const badge = FINDING_BADGE[finding.kind];
        const to = findingLink(finding);
        return (
          <li key={`${finding.kind}:${finding.cycle}:${finding.title}:${index}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 py-2.5 first:pt-0">
            <Badge tone={badge.tone}>{badge.label}</Badge>
            {to ? (
              <Link to={to} className="-my-1 inline-block break-all py-1 font-medium text-brand-300 hover:underline">
                {finding.title}
              </Link>
            ) : (
              <span className="font-medium text-ink-100">{finding.title}</span>
            )}
            <span className="text-xs text-ink-400">
              {finding.cycle} · {finding.detail}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ByCycle({ rows }: { rows: ProjectHealthResponse["by_cycle"] }) {
  return (
    <div className="-mx-5 overflow-x-auto px-5">
      <table className="w-full min-w-[34rem] text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-ink-400">
          <tr>
            <th scope="col" className="pb-2 font-semibold">Cycle</th>
            <th scope="col" className="pb-2 font-semibold">Score</th>
            <th scope="col" className="pb-2 font-semibold">Latest run</th>
            <th scope="col" className="pb-2 font-semibold">Green runs</th>
            <th scope="col" className="pb-2 font-semibold">Flaky</th>
            <th scope="col" className="pb-2 font-semibold">Slower</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((row) => (
            <tr key={row.cycle}>
              <td className="py-2 pr-3">
                <Link to={`/health?cycle=${encodeURIComponent(row.cycle)}`} className="font-mono text-brand-300 hover:underline">
                  {row.cycle}
                </Link>
              </td>
              <td className={`py-2 pr-3 font-semibold ${TONE_TEXT[scoreTone(row.score)]}`}>{row.score ?? "–"}</td>
              <td className="py-2 pr-3">
                <Link to={`/runs/${row.run_id}`} className="hover:underline">
                  {row.passed_latest ? (
                    <span className="text-success-300">Passed</span>
                  ) : (
                    <span className="text-danger-300">{row.failing} failing</span>
                  )}
                </Link>
              </td>
              <td className="py-2 pr-3 text-ink-200">
                {row.green_runs} of {row.runs}
              </td>
              <td className="py-2 pr-3 text-ink-200">{row.flaky}</td>
              <td className="py-2 text-ink-200">{row.slower}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CyclePicker({ cycle, onChange }: { cycle: string; onChange: (cycle: string) => void }) {
  const cycles = useQuery({ queryKey: ["cycles"], queryFn: () => apiClient.listCycles() });
  return (
    <label className="flex items-center gap-2 text-sm text-ink-300">
      Scope
      <select
        value={cycle}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-ink-100"
      >
        <option value="">All cycles</option>
        {(cycles.data?.items ?? []).map((item) => (
          <option key={item.name} value={item.name}>
            {item.name}
          </option>
        ))}
        {cycle && !cycles.data?.items.some((item) => item.name === cycle) ? <option value={cycle}>{cycle}</option> : null}
      </select>
    </label>
  );
}

/**
 * How healthy the tests of the project under test are: a repeatable score
 * from the recorded runs, what to fix first, and an optional AI review whose
 * checks the user switches on and off.
 */
export function ProjectHealthPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const cycle = searchParams.get("cycle") ?? "";
  const query = useQuery({
    queryKey: ["project-health", cycle],
    queryFn: () => apiClient.getProjectHealth(cycle || undefined)
  });

  const setCycle = (next: string) => setSearchParams(next ? { cycle: next } : {});

  return (
    <section className="space-y-6">
      <PageHeader
        title="Project health"
        subtitle="How healthy the tests of the project you test are, scored from the runs QASight recorded."
        actions={<CyclePicker cycle={cycle} onChange={setCycle} />}
      />

      {query.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-ink-300">
          <Spinner /> Scoring the project…
        </div>
      ) : query.isError || !query.data ? (
        <EmptyState mascot={<MuscleShrug />} title="Couldn't load project health" message={query.error instanceof Error ? query.error.message : undefined} />
      ) : query.data.cycles === 0 ? (
        <EmptyState
          mascot={<MuscleShrug />}
          title="No runs to score yet"
          message={
            <>
              Run a cycle or import results first, then come back.{" "}
              <Link to="/cycles" className="text-brand-300 hover:underline">
                Go to cycles
              </Link>
              .
            </>
          }
        />
      ) : (
        <HealthReport health={query.data} />
      )}

      <AiReviewCard cycle={cycle || undefined} />
    </section>
  );
}

function HealthReport({ health }: { health: ProjectHealthResponse }) {
  return (
    <>
      <Card title="Test health score" data-testid="health-score">
        <div className="flex flex-col gap-6 md:flex-row md:items-start">
          <div className="flex flex-col items-center gap-2 md:w-48">
            <ScoreRing score={health.score} grade={health.grade} label="Test health score" />
            <p className="text-center text-xs text-ink-300">
              {countLabel(health.tests, "test")} in {countLabel(health.cycles, "cycle")}, {countLabel(health.runs_considered, "run")}
              {health.latest_run_at ? `. Last run ${formatRunTime(health.latest_run_at)}` : ""}.
            </p>
            <p className="text-center text-[11px] text-ink-400">Same runs, same score: computed from results, no AI.</p>
          </div>
          <div className="min-w-0 flex-1">
            <Signals signals={health.signals} />
          </div>
        </div>
      </Card>

      <Card title="What to fix first">
        <Findings findings={health.findings} />
      </Card>

      {health.by_cycle.length > 1 ? (
        <Card title="By cycle">
          <ByCycle rows={health.by_cycle} />
        </Card>
      ) : null}
    </>
  );
}
