import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient, type TestSummaryItem } from "../../lib/api-client";
import { Badge, Card, Spinner } from "../../components/ui";
import { formatDuration, formatPercent, testPath } from "../../lib/format";
import { hasLabel } from "../labels/labels";
import { TestLabels } from "../labels/TestLabels";

const LIST_LIMIT = 5;
const SEARCH_LIMIT = 25;
// A link carries the names; past this, run the cycle (or `qasight run --test`).
const RUN_LIMIT = 50;

function runTestsQuery(tests: TestSummaryItem[]): string {
  const params = new URLSearchParams({ run: "1" });
  for (const test of tests) params.append("test", test.full_name || test.name);
  return params.toString();
}

function TestLink({ cycle, test }: { cycle: string; test: TestSummaryItem }) {
  return (
    <Link to={testPath(cycle, test.key)} className="break-all text-brand-300 hover:underline">
      {test.name}
    </Link>
  );
}

function matches(test: TestSummaryItem, needle: string): boolean {
  return [test.name, test.full_name, test.group, ...(test.labels ?? []).map((label) => label.value)].some((field) =>
    field.toLowerCase().includes(needle)
  );
}

function TestRow({ cycle, test }: { cycle: string; test: TestSummaryItem }) {
  return (
    <li className="flex flex-wrap items-center gap-2">
      <TestLink cycle={cycle} test={test} />
      <Badge tone={test.last_status === "passed" ? "success" : "danger"}>{test.last_status}</Badge>
      {test.flaky ? <Badge tone="brand">Flaky</Badge> : null}
      <TestLabels labels={test.labels} links={test.links} />
    </li>
  );
}

/** Find a test, and see the slowest ones and the ones that got slower, across the cycle's recent runs. */
export function CycleTestsCard({ cycle, runnable = false }: { cycle: string; runnable?: boolean }) {
  const [search, setSearch] = useState("");
  const [params, setParams] = useSearchParams();
  const label = params.get("label") ?? "";
  const query = useQuery({
    queryKey: ["cycle-tests", cycle],
    queryFn: () => apiClient.getCycleTests(cycle)
  });

  const loaded = Boolean(query.data);
  useEffect(() => {
    // A label chip elsewhere links here with ?label=; bring the filtered list into view.
    if (label && loaded) document.getElementById("tests")?.scrollIntoView?.({ block: "start" });
  }, [label, loaded]);

  if (query.isLoading) {
    return (
      <Card title="Tests">
        <div className="flex items-center gap-2 text-sm text-ink-300">
          <Spinner /> Loading tests…
        </div>
      </Card>
    );
  }
  if (query.isError || !query.data || query.data.items.length === 0) {
    return null;
  }

  const tests = query.data.items;
  const needle = search.trim().toLowerCase();
  const found = needle ? tests.filter((test) => matches(test, needle)) : [];
  const labelled = label ? tests.filter((test) => hasLabel(test.labels, label)) : [];
  const clearLabel = () => {
    const next = new URLSearchParams(params);
    next.delete("label");
    setParams(next, { replace: true });
  };
  const slowest = [...tests]
    .filter((test) => test.median_duration_ms != null)
    .sort((a, b) => (b.median_duration_ms ?? 0) - (a.median_duration_ms ?? 0))
    .slice(0, LIST_LIMIT);
  const slower = tests
    .filter((test) => test.slowdown_ms != null)
    .sort((a, b) => (b.slowdown_ms ?? 0) - (a.slowdown_ms ?? 0))
    .slice(0, LIST_LIMIT);

  return (
    <Card title={`Tests (${tests.length})`} id="tests">
      {label ? (
        <div className="mb-4 rounded-xl border border-white/10 bg-ink-900/60 p-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold text-white">
              {labelled.length} {labelled.length === 1 ? "test" : "tests"} labelled "{label}"
            </h3>
            <span className="flex flex-wrap items-center gap-3">
              {runnable && labelled.length > 0 && labelled.length <= RUN_LIMIT ? (
                <Link
                  to={`?${runTestsQuery(labelled)}`}
                  className="text-xs font-medium text-brand-300 hover:underline"
                >
                  Run {labelled.length === 1 ? "it" : `these ${labelled.length}`}
                </Link>
              ) : null}
              <button type="button" onClick={clearLabel} className="text-xs text-brand-300 hover:underline">
                Show all tests
              </button>
            </span>
          </div>
          {labelled.length === 0 ? (
            <p className="text-sm text-ink-300">No test in the recent runs has this label.</p>
          ) : (
            <ul className="space-y-1.5 text-sm" aria-label="Tests with this label">
              {labelled.slice(0, SEARCH_LIMIT).map((test) => (
                <TestRow key={test.key} cycle={cycle} test={test} />
              ))}
              {labelled.length > SEARCH_LIMIT ? (
                <li className="text-xs text-ink-400">and {labelled.length - SEARCH_LIMIT} more</li>
              ) : null}
            </ul>
          )}
        </div>
      ) : null}
      <label className="grid gap-1 text-xs font-medium text-ink-300">
        Find a test
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Name, module, class or label"
          className="w-full min-w-0 rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 text-sm text-ink-100 focus:border-brand-500"
        />
      </label>

      {needle ? (
        found.length === 0 ? (
          <p className="mt-3 text-sm text-ink-300">No test matches "{search.trim()}".</p>
        ) : (
          <ul className="mt-3 space-y-1.5 text-sm" aria-label="Matching tests">
            {found.slice(0, SEARCH_LIMIT).map((test) => (
              <TestRow key={test.key} cycle={cycle} test={test} />
            ))}
            {found.length > SEARCH_LIMIT ? (
              <li className="text-xs text-ink-400">and {found.length - SEARCH_LIMIT} more: type more of the name</li>
            ) : null}
          </ul>
        )
      ) : null}

      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <div>
          <h3 className="mb-2 text-sm font-semibold text-white">Slowest tests</h3>
          {slowest.length === 0 ? (
            <p className="text-sm text-ink-400">No durations recorded.</p>
          ) : (
            <ol className="space-y-1.5 text-sm">
              {slowest.map((test) => (
                <li key={test.key} className="flex items-baseline justify-between gap-3">
                  <TestLink cycle={cycle} test={test} />
                  <span className="shrink-0 text-xs text-ink-300">{formatDuration(test.median_duration_ms)}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
        <div>
          <h3 className="mb-2 text-sm font-semibold text-white">Got slower</h3>
          {slower.length === 0 ? (
            <p className="text-sm text-ink-400">No test got slower beyond timing noise.</p>
          ) : (
            <ol className="space-y-1.5 text-sm">
              {slower.map((test) => (
                <li key={test.key} className="flex items-baseline justify-between gap-3">
                  <TestLink cycle={cycle} test={test} />
                  <span className="shrink-0 text-xs text-warn-300">
                    +{formatDuration(test.slowdown_ms)}
                    {test.slowdown_pct != null ? ` (+${formatPercent(test.slowdown_pct)})` : ""}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
      <p className="mt-4 text-xs text-ink-400">
        Across the last {query.data.runs_considered} runs. Slowest is by median time; got slower compares the last three runs
        with the ones before.
      </p>
    </Card>
  );
}
