import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Card } from "../../components/ui";
import { apiClient, type Delivery } from "../../lib/api-client";
import { formatSpan } from "../../lib/format";

/** "was 4.0d", coloured by whether it got better; lower is better for all three numbers here. */
function Was({ now, before, text }: { now: number | null; before: number | null; text: string }) {
  if (now === null || before === null) return null;
  const tone = Math.abs(now - before) < 1e-9 ? "text-ink-400" : now < before ? "text-success-400" : "text-danger-400";
  return <span className={tone}> · was {text}</span>;
}

function Stat({ label, value, sub, testId }: { label: string; value: string; sub: ReactNode; testId: string }) {
  return (
    <div className="inset-tile min-w-0 px-4 py-3" data-testid={testId}>
      <p className="text-xs font-medium text-ink-300">{label}</p>
      <p className="my-0.5 text-lg font-bold tabular-nums text-white">{value}</p>
      <p className="text-xs text-ink-400">{sub}</p>
    </div>
  );
}

export function DeliveryStats({ delivery: d }: { delivery: Delivery }) {
  const weekPlus = d.open_by_age.slice(2).reduce((n, b) => n + b.count, 0);
  const branch = d.default_branch ?? "Default branch";
  return (
    <div className="grid grid-cols-2 gap-3">
      <Stat
        testId="delivery-fix"
        label="Time to fix"
        value={formatSpan(d.median_fix_s)}
        sub={
          d.fixes ? (
            <>
              median of {d.fixes} {d.fixes === 1 ? "fix" : "fixes"}
              <Was now={d.median_fix_s} before={d.prev_median_fix_s} text={formatSpan(d.prev_median_fix_s)} />
            </>
          ) : (
            "No failing test was fixed"
          )
        }
      />
      <Stat
        testId="delivery-open"
        label="Open failures"
        value={String(d.open_failures)}
        sub={d.open_failures ? `${weekPlus} older than a week` : "Nothing failing"}
      />
      <Stat
        testId="delivery-red"
        label={`${branch} red`}
        value={d.red_pct === null ? "–" : `${Math.round(d.red_pct)}%`}
        sub={
          d.red_pct === null ? (
            "No runs on it"
          ) : (
            <>
              {d.longest_red_s ? `longest spell ${formatSpan(d.longest_red_s)}` : "of the time"}
              <Was now={d.red_pct} before={d.prev_red_pct} text={d.prev_red_pct === null ? "" : `${Math.round(d.prev_red_pct)}%`} />
            </>
          )
        }
      />
      <Stat
        testId="delivery-ci"
        label="CI time"
        value={`${Math.round(d.ci_minutes)} min`}
        sub={
          <>
            all test runs together
            <Was now={d.ci_minutes} before={d.prev_ci_minutes || null} text={`${Math.round(d.prev_ci_minutes)} min`} />
          </>
        }
      />
    </div>
  );
}

/** The dashboard's "Last 7 days" card: how long tests stay red, main branch red time and CI time. */
export function DeliveryCard() {
  const query = useQuery({ queryKey: ["dashboard-delivery"], queryFn: () => apiClient.getDashboardDelivery(7) });
  if (!query.data) return null; // loading, an older server without the route, or an error: the rest of the page still works
  return (
    <Card
      title="Last 7 days"
      actions={
        <Link to="/report" className="text-xs text-brand-300 hover:underline">
          Full report
        </Link>
      }
    >
      <DeliveryStats delivery={query.data} />
    </Card>
  );
}
