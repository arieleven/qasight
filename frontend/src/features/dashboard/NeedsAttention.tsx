import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient, type DashboardAttentionResponse } from "../../lib/api-client";
import { Badge, Card, ShowMoreButton, useShowMore } from "../../components/ui";
import { countLabel, formatDuration, formatPercent, testPath } from "../../lib/format";

function FailingCycles({ failing }: { failing: DashboardAttentionResponse["failing"] }) {
  const rows = useShowMore(failing);
  return (
    <>
      <ul className="divide-y divide-white/5">
        {rows.visible.map((item) => (
          <li key={item.cycle} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5 first:pt-0">
            <Link to={`/runs/${item.run_id}`} className="-my-1 inline-block py-1 font-semibold text-brand-300 hover:underline">
              {item.cycle}
            </Link>
            <span className="text-sm text-ink-200">
              {item.failed_tests === 0
                ? // Red with no failing test: a stage crashed before or outside its tests.
                  "failed outside its tests"
                : `${countLabel(item.failed_tests, "test")} failing${item.new_failures > 0 ? `, ${item.new_failures} new` : ""}`}
            </span>
            {item.new_failures > 0 ? <Badge tone="danger">New failures</Badge> : null}
            {item.failing_runs > 1 ? <Badge tone="warn">Red for {item.failing_runs} runs</Badge> : null}
            {item.branch ? <span className="font-mono text-xs text-ink-400">{item.branch}</span> : null}
          </li>
        ))}
      </ul>
      <ShowMoreButton className="mt-2" total={rows.total} expanded={rows.expanded} onToggle={rows.setExpanded} />
    </>
  );
}

function TestList({
  title,
  tests,
  detail
}: {
  title: string;
  tests: DashboardAttentionResponse["slower"];
  detail?: (test: DashboardAttentionResponse["slower"][number]) => string;
}) {
  const rows = useShowMore(tests);
  if (tests.length === 0) return null;
  return (
    <div>
      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-400">{title}</h3>
      <ul className="space-y-1 text-sm">
        {rows.visible.map((test) => (
          <li key={`${test.cycle}:${test.key}`} className="flex flex-wrap items-baseline gap-x-2">
            <Link to={testPath(test.cycle, test.key)} className="-my-1 inline-block break-all py-1 text-brand-300 hover:underline">
              {test.name}
            </Link>
            <span className="text-xs text-ink-400">
              {[test.cycle, detail?.(test)].filter(Boolean).join(" · ")}
            </span>
          </li>
        ))}
      </ul>
      <ShowMoreButton className="mt-2" total={rows.total} expanded={rows.expanded} onToggle={rows.setExpanded} />
    </div>
  );
}

function slowdown(test: DashboardAttentionResponse["slower"][number]): string {
  if (test.slowdown_ms == null) return "";
  const pct = test.slowdown_pct != null ? ` (+${formatPercent(test.slowdown_pct)})` : "";
  return `+${formatDuration(test.slowdown_ms)}${pct}`;
}

/**
 * The dashboard's first card: the answer to "does anything need me?". Red
 * cycles (most new failures first), tests that just started flaking, and
 * tests that got slower, each linking to the page that explains it.
 */
export function NeedsAttention() {
  const query = useQuery({ queryKey: ["dashboard-attention"], queryFn: () => apiClient.getDashboardAttention() });
  // The rest of the dashboard still answers on its own; this card just steps aside.
  if (query.isLoading || query.isError || !query.data || query.data.cycles === 0) return null;

  const { cycles, passing_cycles: passing, failing, newly_flaky: flaky, slower } = query.data;
  const quiet = failing.length === 0 && flaky.length === 0 && slower.length === 0;

  const summary =
    failing.length > 0
      ? `${countLabel(failing.length, "cycle")} red`
      : flaky.length + slower.length > 0
        ? `${countLabel(flaky.length + slower.length, "test")} to look at`
        : "Nothing needs you";

  return (
    <Card title="Needs attention" data-testid="needs-attention" collapsible collapsedSummary={summary}>
      <p className={`text-sm ${failing.length ? "text-ink-200" : "text-success-300"}`}>
        {failing.length === 0
          ? cycles === 1
            ? "Your cycle passed its latest run."
            : `All ${countLabel(cycles, "cycle")} passed their latest run.`
          : passing === 0
            ? `${cycles === 1 ? "The only cycle is" : `All ${countLabel(cycles, "cycle")} are`} red.`
            : `${passing} of ${countLabel(cycles, "cycle")} passed their latest run; ${countLabel(failing.length, "cycle")} ${failing.length === 1 ? "is" : "are"} red.`}
        {quiet ? " Nothing needs you." : ""}
      </p>
      {failing.length > 0 ? (
        <div className="mt-3">
          <FailingCycles failing={failing} />
        </div>
      ) : null}
      {flaky.length > 0 || slower.length > 0 ? (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <TestList title="Started flaking" tests={flaky} />
          <TestList title="Got slower" tests={slower} detail={slowdown} />
        </div>
      ) : null}
    </Card>
  );
}
