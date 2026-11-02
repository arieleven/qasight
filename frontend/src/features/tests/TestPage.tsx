import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { apiClient, type EnvironmentOutcomeItem, type TestPointItem } from "../../lib/api-client";
import { Badge, Card, KeyValue, PageHeader, Spinner, StatusPill } from "../../components/ui";
import { countLabel, formatDuration, formatRunTime, shortRunId } from "../../lib/format";
import { IS_STATIC_BUILD } from "../../lib/static-backend";
import { QuarantineControl, matchingEntry } from "../quarantine/QuarantineControl";
import { Attachments } from "./Attachments";
import { TestLabels } from "../labels/TestLabels";

const FAILED = new Set(["failed", "broken"]);

/** "Oct 31" for the x axis, or "10:33" when every run is from the same day; the full time is in each tooltip. */
function axisLabels(points: TestPointItem[]): string[] {
  const days = points.map((p) => new Date(p.created_at * 1000).toDateString());
  const sameDay = new Set(days).size === 1;
  return points.map((p) => {
    const date = new Date(p.created_at * 1000);
    return sameDay
      ? date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  });
}

/** Label every bar when there is room, else every nth, always the first and last. */
function labelEvery(count: number): number {
  return Math.max(1, Math.ceil(count / 8));
}

/** One bar per run, oldest on the left, height by duration and colour by outcome; each bar opens its run. */
function DurationBars({ points }: { points: TestPointItem[] }) {
  const oldestFirst = [...points].reverse();
  const longest = Math.max(1, ...oldestFirst.map((p) => p.duration_ms ?? 0));
  const every = labelEvery(oldestFirst.length);
  const labels = axisLabels(oldestFirst);
  return (
    <div className="flex gap-2">
      {/* The scale, so equal durations don't read as a busy chart. */}
      <div className="flex h-28 flex-col justify-between pb-5 text-right font-mono text-[11px] text-ink-400" aria-hidden>
        <span>{formatDuration(longest)}</span>
        <span>{formatDuration(longest / 2)}</span>
        <span>0</span>
      </div>
      <div className="min-w-0 flex-1">
        <ul
          className="flex h-24 items-end gap-1 border-b border-l border-white/10 sm:gap-2"
          aria-label={`Duration per run, oldest first: ${oldestFirst.map((p) => `${formatDuration(p.duration_ms)} ${p.status}`).join(", ")}`}
        >
          {oldestFirst.map((p) => (
            <li key={p.run_id} className="flex h-full min-w-0 flex-1 items-end">
              <Link
                to={`/runs/${p.run_id}`}
                title={`${formatRunTime(p.created_at)} · ${p.status} · ${formatDuration(p.duration_ms)}`}
                aria-label={`${formatRunTime(p.created_at)}: ${formatDuration(p.duration_ms)}, ${p.status}, open run ${shortRunId(p.run_id)}`}
                className={`block w-full rounded-t hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-brand-400 ${
                  p.status === "passed" ? "bg-success-500/80" : FAILED.has(p.status) ? "bg-danger-500/80" : "bg-ink-600"
                }`}
                style={{ height: `${Math.max(4, ((p.duration_ms ?? 0) / longest) * 100)}%` }}
              />
            </li>
          ))}
        </ul>
        <ol className="mt-1 flex gap-1 sm:gap-2" aria-hidden>
          {oldestFirst.map((p, i) => (
            <li key={p.run_id} className="min-w-0 flex-1 truncate text-center font-mono text-[10px] text-ink-500">
              {i % every === 0 || i === oldestFirst.length - 1 ? labels[i] : ""}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

// Runs the API measures each point's flip rate over (test_history.FLIP_WINDOW).
const FLIP_WINDOW = 5;

function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/**
 * How flaky the test has been over time: a line of flip rate per run (0 to
 * 100 %), the share of outcome changes over the five runs ending there.
 * Shown only once the test has flipped at all, with a sentence comparing now
 * with five runs before.
 */
/** Pass and fail counts per environment, when the runs recorded one: "fails only on firefox" at a glance. */
export function EnvironmentsCard({ environments }: { environments: EnvironmentOutcomeItem[] }) {
  if (environments.length === 0) return null;
  const failingOn = environments.filter((e) => e.failed > 0);
  const summary =
    environments.length > 1 && failingOn.length > 0 && failingOn.length < environments.length
      ? `Fails only on ${failingOn.map((e) => e.environment).join(" and ")}.`
      : null;
  return (
    <Card title="By environment">
      {summary ? <p className="mb-3 text-sm text-warn-300">{summary}</p> : null}
      <ul className="divide-y divide-white/5 text-sm" aria-label="Results by environment">
        {environments.map((e) => (
          <li key={e.environment} className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0">
            <span className="break-all text-ink-100">{e.environment}</span>
            <span className="flex items-center gap-2 text-xs text-ink-300">
              Passed {e.passed} of {countLabel(e.runs, "run")}
              {e.failed > 0 ? <span className="text-danger-300">{e.failed} failed</span> : null}
              <StatusPill status={e.last_status} />
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function FlipRate({ points }: { points: TestPointItem[] }) {
  const oldestFirst = [...points].reverse();
  const rated = oldestFirst.filter((p) => p.flip_rate != null);
  if (!rated.some((p) => (p.flip_rate ?? 0) > 0)) return null;
  const now = rated[rated.length - 1].flip_rate ?? 0;
  const before = rated.length > FLIP_WINDOW ? rated[rated.length - 1 - FLIP_WINDOW].flip_rate : null;
  const trend =
    before == null
      ? ""
      : now > before
        ? `, up from ${pct(before)} five runs before`
        : now < before
          ? `, down from ${pct(before)} five runs before`
          : ", the same as five runs before";

  // x by run (oldest left) as a percentage of the width, y by rate; runs without a rate yet leave a gap at the start.
  const step = oldestFirst.length > 1 ? 100 / (oldestFirst.length - 1) : 0;
  const coords = oldestFirst
    .map((p, i) => (p.flip_rate == null ? null : { x: i * step, y: 100 - p.flip_rate * 100, p }))
    .filter((c): c is { x: number; y: number; p: TestPointItem } => c != null);
  const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
  const every = labelEvery(oldestFirst.length);
  const labels = axisLabels(oldestFirst);
  const textAlternative = `Flip rate per run, oldest first: ${oldestFirst.map((p) => (p.flip_rate == null ? "n/a" : pct(p.flip_rate))).join(", ")}`;

  return (
    <Card title="Flakiness over time">
      <div className="flex gap-2">
        <div className="flex h-28 flex-col justify-between pb-5 text-right font-mono text-[11px] text-ink-400" aria-hidden>
          <span>100%</span>
          <span>50%</span>
          <span>0</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="relative h-24 border-b border-l border-white/10" role="img" aria-label={textAlternative}>
            {/* The line stretches to the box; the dots are HTML so they stay round. */}
            <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden>
              <line x1="0" y1="50" x2="100" y2="50" className="stroke-white/10" strokeWidth="1" vectorEffect="non-scaling-stroke" />
              {coords.length > 1 ? (
                <polyline points={line} fill="none" className="stroke-brand-400" strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
              ) : null}
            </svg>
            {coords.map((c) => (
              <span
                key={c.p.run_id}
                title={`${formatRunTime(c.p.created_at)} · flip rate ${pct(c.p.flip_rate ?? 0)}`}
                className="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink-950 bg-brand-300"
                style={{ left: `${c.x}%`, top: `${c.y}%` }}
              />
            ))}
          </div>
          <ol className="mt-1 flex" aria-hidden>
            {oldestFirst.map((p, i) => (
              <li key={p.run_id} className="min-w-0 flex-1 truncate text-center font-mono text-[10px] text-ink-500">
                {i % every === 0 || i === oldestFirst.length - 1 ? labels[i] : ""}
              </li>
            ))}
          </ol>
        </div>
      </div>
      <p className="mt-2 text-xs text-ink-400" data-testid="flip-rate-summary">
        Outcome changed in {pct(now)} of the last {FLIP_WINDOW} runs{trend}.
      </p>
    </Card>
  );
}

/** One test across its cycle's recent runs: outcome per run, duration trend and its last failure. */
export function TestPage() {
  const { name: cycle = "" } = useParams();
  const [searchParams] = useSearchParams();
  const key = searchParams.get("key") ?? "";
  const query = useQuery({
    queryKey: ["test-history", cycle, key],
    queryFn: () => apiClient.getTestHistory(cycle, key),
    enabled: Boolean(cycle && key)
  });
  // The list kept on the server; the read-only demo has none.
  const quarantine = useQuery({
    queryKey: ["quarantine", cycle],
    queryFn: () => apiClient.listQuarantine(cycle),
    enabled: Boolean(cycle) && !IS_STATIC_BUILD
  });

  const back = (
    <Link to={`/cycles/${encodeURIComponent(cycle)}`} className="text-sm text-brand-300 hover:underline">
      Back to {cycle || "the cycle"}
    </Link>
  );

  if (!key) {
    return <p className="text-sm text-ink-300">No test picked. {back}</p>;
  }
  if (query.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-ink-300">
        <Spinner /> Loading test history…
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <p className="text-sm text-danger-400">
        Couldn't find this test in the recent runs of {cycle}. It may have been renamed or removed. {back}
      </p>
    );
  }

  const {
    test,
    points,
    last_failure: lastFailure,
    last_failure_attachments: attachments = [],
    owners,
    issue_url: issueUrl,
    environments = []
  } = query.data;
  const passRate = test.runs > 0 ? Math.round((100 * test.passed) / test.runs) : null;
  // An older server answers without the list: then the page shows no quarantine control.
  const storedItems = Array.isArray(quarantine.data?.items) ? quarantine.data.items : null;
  const stored = storedItems ? matchingEntry(storedItems, test.name, test.full_name) : null;

  return (
    <section className="space-y-4">
      <PageHeader
        documentTitle={test.name}
        title={
          <span className="flex flex-wrap items-center gap-3">
            <span className="break-all">{test.name}</span>
            <span className="flex items-center gap-1.5 text-xs font-normal text-ink-400">
              Last run: <StatusPill status={test.last_status} />
            </span>
            {test.flaky ? <Badge tone="brand">Flaky</Badge> : null}
          </span>
        }
        subtitle={
          passRate != null
            ? `Passed ${test.passed} of ${countLabel(test.runs, "run")} (${passRate}%) in ${cycle}`
            : `No recorded outcome in ${cycle}`
        }
        actions={
          <span className="flex flex-wrap items-center gap-3">
            {storedItems ? (
              <QuarantineControl
                cycle={cycle}
                test={test.name}
                quarantined={stored != null}
                entry={stored && !stored.is_glob ? stored.test : null}
                why={
                  stored
                    ? [stored.reason, stored.is_glob ? `matched by ${stored.test}` : "", stored.added_by ? `by ${stored.added_by}` : ""]
                        .filter(Boolean)
                        .join(" · ") || undefined
                    : undefined
                }
              />
            ) : null}
            {issueUrl ? (
              <a href={issueUrl} target="_blank" rel="noreferrer" className="text-sm text-brand-300 hover:underline">
                Open an issue
              </a>
            ) : null}
            {back}
          </span>
        }
      />

      {lastFailure ? (
        <Card title="Last failure">
          <p className="break-words font-mono text-xs text-danger-300">{lastFailure.message || "No failure message recorded."}</p>
          <p className="mt-2 text-xs text-ink-400">
            <Link to={`/runs/${lastFailure.run_id}`} className="text-brand-300 hover:underline">
              Run {shortRunId(lastFailure.run_id)}
            </Link>{" "}
            · {formatRunTime(lastFailure.created_at)}
            {lastFailure.branch ? ` · ${lastFailure.branch}` : ""}
          </p>
          <Attachments items={attachments} />
        </Card>
      ) : null}

      <Card title="Duration">
        <DurationBars points={points} />
        <p className="mt-2 text-xs text-ink-400">
          Median {formatDuration(test.median_duration_ms)}
          {test.slowdown_ms != null
            ? `, and ${formatDuration(test.slowdown_ms)} slower over the last runs${test.slowdown_pct != null ? ` (+${test.slowdown_pct}%)` : ""}`
            : ""}
          .
        </p>
      </Card>

      <FlipRate points={points} />

      <EnvironmentsCard environments={environments} />

      <Card title={`Runs (${points.length})`}>
        <ul className="divide-y divide-white/5 text-sm">
          {points.map((p) => (
            <li key={p.run_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0">
              <StatusPill status={p.status} className="w-20" />
              <Link to={`/runs/${p.run_id}`} className="text-brand-300 hover:underline">
                {formatRunTime(p.created_at)}
              </Link>
              <span className="font-mono text-xs text-ink-500">{shortRunId(p.run_id)}</span>
              <span className="text-xs text-ink-400">{formatDuration(p.duration_ms)}</span>
              {p.branch ? <span className="font-mono text-xs text-ink-400">{p.branch}</span> : null}
              {p.environment ? <span className="text-xs text-ink-400">{p.environment}</span> : null}
              {p.message && FAILED.has(p.status) ? (
                <span className="w-full break-words font-mono text-xs text-danger-300">{p.message}</span>
              ) : null}
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Details">
        <KeyValue
          items={[
            { label: "Full name", value: <span className="break-all font-mono text-xs">{test.full_name || "—"}</span> },
            { label: "Group", value: test.group },
            { label: "Stage", value: test.stage || "—" },
            {
              label: "Labels",
              value:
                (test.labels ?? []).length > 0 || (test.links ?? []).length > 0 ? (
                  <TestLabels labels={test.labels} links={test.links} cycle={cycle} />
                ) : (
                  "None in the results"
                )
            },
            { label: "Owners", value: owners.length > 0 ? owners.join(", ") : "No CODEOWNERS rule matches this test" }
          ]}
        />
      </Card>
    </section>
  );
}
