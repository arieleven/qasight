import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient, type FlakyTestItem } from "../../lib/api-client";
import { Badge, Button, Card, Spinner } from "../../components/ui";
import { testPath } from "../../lib/format";
import { QuarantineControl } from "../quarantine/QuarantineControl";

const DOT: Record<string, string> = {
  passed: "bg-success-500",
  failed: "bg-danger-500",
  broken: "bg-danger-500"
};

function History({ item }: { item: FlakyTestItem }) {
  return (
    <span className="flex flex-wrap gap-0.5" role="img" aria-label={`History, oldest first: ${item.history.join(", ")}`}>
      {item.history.map((status, idx) => (
        <span
          // History is positional; the index is the identity.
          key={idx}
          title={status}
          className={`h-2.5 w-2.5 rounded-sm ${DOT[status] ?? "bg-ink-600"}`}
        />
      ))}
    </span>
  );
}

// A big suite can have hundreds of flaky tests; the worst ones come first.
const FIRST_SHOWN = 10;

/** Flaky tests in this cycle's recent runs, and the broken ones kept apart from them. */
export function FlakyTestsCard({ cycle }: { cycle: string }) {
  const [showAll, setShowAll] = useState(false);
  const query = useQuery({
    queryKey: ["flaky", cycle],
    queryFn: () => apiClient.getFlakyTests(cycle)
  });

  if (query.isLoading) {
    return (
      <Card title="Flaky tests">
        <div className="flex items-center gap-2 text-sm text-ink-300">
          <Spinner /> Checking run history…
        </div>
      </Card>
    );
  }
  if (query.isError || !query.data) {
    return null;
  }

  const report = query.data;
  const summary = `${report.tests_analyzed} tests across the last ${report.runs_analyzed} runs. Flaky means it passed on a retry, passed and failed on the same commit, or flipped at least twice on one branch.`;
  const broken = report.broken ?? [];

  return (
    <Card title={`Flaky tests (${report.items.length})`}>
      <p className="mb-3 text-xs text-ink-400">{summary}</p>
      {report.runs_analyzed < 2 ? (
        <p className="text-sm text-ink-300">Run this cycle a few more times to see which tests are flaky.</p>
      ) : report.items.length === 0 ? (
        <p className="text-sm text-ink-300">No flaky tests.</p>
      ) : (
        <ul className="space-y-2">
          {(showAll ? report.items : report.items.slice(0, FIRST_SHOWN)).map((item) => (
            <li key={item.key} className="rounded-2xl border border-white/5 bg-ink-950/70 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Link to={testPath(cycle, item.key)} className="break-all font-mono text-sm text-brand-300 hover:underline">
                  {item.name}
                </Link>
                <Badge tone="warn">
                  {item.flips} flips in {item.runs} runs
                </Badge>
                <Badge tone={item.last_status === "passed" ? "success" : "danger"}>last: {item.last_status}</Badge>
                {item.cost_minutes ? (
                  <Badge tone="danger" title="CI minutes of its retries plus its share of the red runs only flaky tests caused">
                    cost {costLabel(item.cost_minutes)}
                    {item.red_runs ? ` · ${item.red_runs} red ${item.red_runs === 1 ? "run" : "runs"}` : ""}
                  </Badge>
                ) : null}
                <QuarantineControl
                  cycle={cycle}
                  test={item.name}
                  quarantined={item.quarantined ?? false}
                  entry={item.quarantine_entry ?? null}
                  why={
                    item.quarantined && !item.quarantine_entry
                      ? "Matched by a glob or a testosterone.yaml rule: change it there to release this test."
                      : undefined
                  }
                />
              </div>
              <div className="mt-1 text-xs text-ink-400">
                {item.group}
                {item.owners.length > 0 ? ` · owned by ${item.owners.join(", ")}` : ""}
              </div>
              {(item.reasons ?? []).length > 0 ? (
                <div className="mt-1 text-xs text-ink-300">Why: {(item.reasons ?? []).join("; ")}</div>
              ) : null}
              <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                <History item={item} />
                {item.issue_url ? (
                  <a href={item.issue_url} target="_blank" rel="noreferrer" className="text-xs text-brand-300 hover:underline">
                    Open an issue
                  </a>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
      {!showAll && report.items.length > FIRST_SHOWN ? (
        <Button variant="secondary" size="sm" className="mt-3" onClick={() => setShowAll(true)}>
          Show all {report.items.length}
        </Button>
      ) : null}
      {broken.length > 0 ? (
        <div className="mt-4">
          <h3 className="text-sm font-semibold text-ink-100">Broken, not flaky ({broken.length})</h3>
          <p className="mb-2 text-xs text-ink-400">
            These failed every one of their last runs. Fix them rather than quarantine them.
          </p>
          <ul className="space-y-1">
            {broken.map((item) => (
              <li key={item.key} className="text-sm">
                <Link to={testPath(cycle, item.key)} className="font-mono text-brand-300 hover:underline">
                  {item.name}
                </Link>{" "}
                <span className="text-xs text-ink-400">
                  failing {item.failing_runs} runs in a row{item.message ? ` · ${item.message}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

/** "45 s", "12 min", "3.5 h": CI time a flaky test cost. */
function costLabel(minutes: number): string {
  if (minutes < 1) return `${Math.max(1, Math.round(minutes * 60))} s`;
  if (minutes < 120) return `${Math.round(minutes)} min`;
  return `${(minutes / 60).toFixed(1)} h`;
}
