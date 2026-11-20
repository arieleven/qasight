import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient, type ProjectHealthResponse } from "../../lib/api-client";
import { Card, EmptyState, PageHeader, Spinner } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { countLabel, formatRunTime } from "../../lib/format";
import { AiReviewCard } from "./AiReviewCard";
import { ByCycle, Findings, Signals } from "./HealthParts";
import { ScoreRing } from "./score";

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
        title="Test health"
        subtitle="How healthy your tests are, scored 0-100 from the runs QASight recorded: passing, green builds, flakiness, speed, hygiene and the test pyramid. The code itself is graded under Releases → Code quality."
        actions={<CyclePicker cycle={cycle} onChange={setCycle} />}
      />

      {query.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-ink-300">
          <Spinner /> Scoring the project…
        </div>
      ) : query.isError || !query.data ? (
        <EmptyState mascot={<MuscleShrug />} title="Couldn't load test health" message={query.error instanceof Error ? query.error.message : undefined} />
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
