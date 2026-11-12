import { useQuery } from "@tanstack/react-query";

import { LiveRunItem, apiClient } from "../../lib/api-client";
import { Card } from "../../components/ui";
import { formatDuration } from "../../lib/format";

const LIVE_REFRESH_MS = 3_000;
const FAILURES_SHOWN = 3;

/**
 * Test sessions still running (`pytest --qasight-server`), result by result. Renders
 * nothing when none run, and stops polling where the API has no live sessions
 * (an older server, the static demo).
 */
export function LiveRunsCard() {
  const query = useQuery({
    queryKey: ["live-runs"],
    queryFn: () => apiClient.listLiveRuns(),
    retry: false,
    refetchInterval: (q) => (q.state.status === "error" ? false : LIVE_REFRESH_MS)
  });
  const items = query.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <Card title="Running now" aria-live="polite">
      <ul className="space-y-4">
        {items.map((item) => (
          <LiveRunRow key={item.id} item={item} />
        ))}
      </ul>
    </Card>
  );
}

export function LiveRunRow({ item }: { item: LiveRunItem }) {
  const pct = item.total ? Math.min(100, Math.round((item.done / item.total) * 100)) : null;
  const red = item.failed + item.errors;
  return (
    <li className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="font-semibold text-white">
          {item.cycle}
          {item.branch ? <span className="ml-2 font-normal text-ink-300">{item.branch}</span> : null}
        </span>
        <span className="text-ink-300">
          {item.finished ? "finished, uploading the report" : `${formatDuration(item.elapsed_s * 1000)} so far`}
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-ink-800"
        role="progressbar"
        aria-label={`${item.cycle} progress`}
        aria-valuemin={0}
        aria-valuemax={item.total ?? undefined}
        aria-valuenow={item.done}
      >
        <span
          className={`block h-full rounded-full ${red ? "bg-danger-400" : "bg-success-400"} ${pct == null ? "animate-pulse" : ""}`}
          style={{ width: `${pct ?? 100}%` }}
        />
      </div>
      <p className="text-xs text-ink-300">
        {item.total ? `${item.done} of ${item.total} tests` : `${item.done} tests`}
        {" · "}
        <span className="text-success-300">{item.passed} passed</span>
        {red ? <span className="text-danger-300"> · {red} failed</span> : null}
        {item.skipped ? ` · ${item.skipped} skipped` : null}
        {item.current && !item.finished ? <span className="block truncate">Last: {item.current}</span> : null}
      </p>
      {item.failures.length ? (
        <ul className="space-y-1">
          {item.failures.slice(0, FAILURES_SHOWN).map((failure) => (
            <li key={failure.test} className="rounded-md border border-danger-500/30 bg-danger-500/10 px-2 py-1 text-xs">
              <span className="block truncate font-medium text-danger-300">{failure.test}</span>
              {failure.message ? (
                <span className="block truncate text-ink-300">{failure.message.split("\n").pop()}</span>
              ) : null}
            </li>
          ))}
          {item.failures.length > FAILURES_SHOWN ? (
            <li className="text-xs text-ink-300">and {item.failures.length - FAILURES_SHOWN} more</li>
          ) : null}
        </ul>
      ) : null}
    </li>
  );
}
