import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient, type RunListItem } from "../../lib/api-client";
import { Card, RunLabel, Spinner, StatusPill } from "../../components/ui";
import { formatRunLabel, passRateLine } from "../../lib/format";

// Enough to see a trend at a glance; the Runs page has the rest.
const STRIP_RUNS = 20;

function outcome(run: RunListItem): string {
  if (run.returncode == null) return "running";
  return run.returncode === 0 ? "passed" : "failed";
}

const SQUARE: Record<string, string> = {
  passed: "bg-success-500",
  failed: "bg-danger-500",
  running: "bg-ink-600"
};

/** This cycle's recent runs: how often it passed, one square per run, and the latest few. */
export function CycleRunsCard({ cycle }: { cycle: string }) {
  const query = useQuery({
    queryKey: ["runs", cycle],
    queryFn: () => apiClient.listRuns(cycle)
  });

  if (query.isLoading) {
    return (
      <Card title="Recent runs">
        <div className="flex items-center gap-2 text-sm text-ink-300">
          <Spinner /> Loading runs…
        </div>
      </Card>
    );
  }
  if (query.isError || !query.data) return null;

  const runs = query.data.items.slice(0, STRIP_RUNS);
  if (runs.length === 0) {
    return (
      <Card title="Recent runs">
        <p className="text-sm text-ink-400">No recorded runs of this cycle yet.</p>
      </Card>
    );
  }
  // Oldest first, so the strip reads left to right like a timeline.
  const timeline = [...runs].reverse();

  return (
    <Card title="Recent runs" data-testid="cycle-runs">
      <p className="mb-3 text-sm text-ink-200">{passRateLine(runs)}</p>
      <ol className="mb-4 flex flex-wrap gap-1" aria-label="Outcomes, oldest first">
        {timeline.map((run) => (
          <li key={run.run_id}>
            <Link
              to={`/runs/${run.run_id}`}
              title={`${formatRunLabel(run)}: ${outcome(run)}`}
              aria-label={`${formatRunLabel(run)}: ${outcome(run)}`}
              className={`block h-6 w-6 rounded ${SQUARE[outcome(run)]} hover:ring-2 hover:ring-brand-300`}
            />
          </li>
        ))}
      </ol>
      <ul className="divide-fade">
        {runs.slice(0, 3).map((run) => (
          <li key={run.run_id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
            <StatusPill status={run.status ?? "unknown"} returncode={run.returncode} />
            <RunLabel run={run} linked />
            {run.branch ? <span className="font-mono text-xs text-ink-400">{run.branch}</span> : null}
            {run.environment ? <span className="text-xs text-ink-400">{run.environment}</span> : null}
          </li>
        ))}
      </ul>
      <Link to={`/runs?cycle=${encodeURIComponent(cycle)}`} className="mt-2 inline-block py-1 text-sm text-brand-300 hover:underline">
        All runs of {cycle}
      </Link>
    </Card>
  );
}
