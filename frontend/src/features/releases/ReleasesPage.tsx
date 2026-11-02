import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import {
  apiClient,
  type ReleaseDetailResponse,
  type ReleaseSummaryItem,
  type ReleaseTestItem
} from "../../lib/api-client";
import { Badge, Card, EmptyState, PageHeader, Spinner } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { countLabel, cyclePath, formatPercent, formatRunTime, testPath } from "../../lib/format";

export function releasePath(release: string): string {
  return `/releases/${encodeURIComponent(release)}`;
}

function HowTo() {
  return (
    <>
      Record the release a run tests with <code>testo run --release 2.3</code> (or <code>testo ingest --release</code>, the{" "}
      <code>TESTO_RELEASE</code> variable, or the GitHub Action&apos;s <code>release</code> input). Each release then gets a page
      with its pass rate, failing and flaky tests, and what changed from the release before.
    </>
  );
}

function Loading({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-ink-300">
      <Spinner /> {text}
    </div>
  );
}

function passTone(item: ReleaseSummaryItem): "success" | "danger" | "neutral" {
  if (item.pass_rate == null) return "neutral";
  return item.failed > 0 ? "danger" : "success";
}

/** Every release a run recorded, newest first: pass rate and failures of its latest runs. */
export function ReleasesPage() {
  const query = useQuery({ queryKey: ["releases"], queryFn: () => apiClient.getReleases() });
  const items = query.data?.items ?? [];

  return (
    <div>
      <PageHeader title="Releases" subtitle="Runs grouped by the release they tested." />
      {query.isLoading ? (
        <Loading text="Loading releases…" />
      ) : query.isError ? (
        <EmptyState
          mascot={<MuscleShrug />}
          title="Couldn't load releases"
          message={query.error instanceof Error ? query.error.message : undefined}
        />
      ) : items.length === 0 ? (
        <EmptyState mascot={<MuscleShrug />} title="No releases recorded yet" message={<HowTo />} />
      ) : (
        <Card title="All releases">
          <ul className="divide-y divide-white/5">
            {items.map((item) => (
              <li key={item.release} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5">
                <Link to={releasePath(item.release)} className="font-semibold text-brand-300 hover:underline">
                  {item.release}
                </Link>
                <span className="flex flex-wrap items-baseline gap-x-3 text-xs text-ink-300">
                  <Badge tone={passTone(item)}>{formatPercent(item.pass_rate)} passed</Badge>
                  {item.failed > 0 ? <span className="text-danger-300">{countLabel(item.failed, "failing test")}</span> : null}
                  <span>
                    {countLabel(item.cycles, "cycle")}, {countLabel(item.runs, "run")}
                  </span>
                  <span>last run {formatRunTime(item.last_run_at)}</span>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function TestList({ tests, empty }: { tests: ReleaseTestItem[]; empty: string }) {
  if (tests.length === 0) return <p className="text-sm text-ink-300">{empty}</p>;
  return (
    <ul className="space-y-2 text-sm">
      {tests.map((test) => (
        <li key={`${test.cycle}:${test.key}`} className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <Link to={testPath(test.cycle, test.key)} className="break-all text-brand-300 hover:underline">
              {test.name}
            </Link>
            <span className="text-xs text-ink-400">{test.cycle}</span>
            {test.new ? <Badge tone="danger">New in this release</Badge> : null}
          </div>
          {test.message ? <p className="break-words text-xs text-ink-300">{test.message}</p> : null}
        </li>
      ))}
    </ul>
  );
}

function Delta({ data }: { data: ReleaseDetailResponse }) {
  const prev = data.previous;
  if (!prev) return <p className="text-sm text-ink-300">This is the first release recorded, so there is nothing to compare with.</p>;
  const now = data.summary.pass_rate;
  const before = prev.pass_rate;
  const change = now != null && before != null ? Math.round((now - before) * 10) / 10 : null;
  const newFailures = data.failing.filter((t) => t.new).length;
  return (
    <p className="text-sm text-ink-200">
      Against{" "}
      <Link to={releasePath(prev.release)} className="text-brand-300 hover:underline">
        {prev.release}
      </Link>
      : pass rate {formatPercent(before)} → {formatPercent(now)}
      {change != null && change !== 0 ? (
        <span className={change > 0 ? "text-success-300" : "text-danger-300"}>
          {" "}
          ({change > 0 ? "+" : ""}
          {change} points)
        </span>
      ) : null}
      , {countLabel(newFailures, "new failure")}, {countLabel(data.fixed.length, "test")} fixed.
    </p>
  );
}

/** One release: each cycle's latest run in it, failing and flaky tests, and the change from the release before. */
export function ReleasePage() {
  const { release = "" } = useParams();
  const query = useQuery({ queryKey: ["release", release], queryFn: () => apiClient.getRelease(release) });
  const data = query.data;

  return (
    <div>
      <PageHeader
        title={`Release ${release}`}
        subtitle={
          data
            ? `${countLabel(data.summary.runs, "run")} across ${countLabel(data.summary.cycles, "cycle")}, ${formatRunTime(
                data.summary.first_run_at
              )} to ${formatRunTime(data.summary.last_run_at)}.`
            : undefined
        }
      />
      {query.isLoading ? (
        <Loading text="Loading the release…" />
      ) : query.isError || !data ? (
        <EmptyState
          mascot={<MuscleShrug />}
          title={`No run recorded release ${release}`}
          message={<HowTo />}
          action={
            <Link to="/releases" className="text-brand-300 hover:underline">
              All releases
            </Link>
          }
        />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Card title="Pass rate" copy={false}>
              <p className="text-3xl font-bold text-white">{formatPercent(data.summary.pass_rate)}</p>
              <p className="text-xs text-ink-300">
                {data.summary.passed} of {data.summary.total - data.summary.skipped} tests, latest run of each cycle
              </p>
            </Card>
            <Card title="Failing" copy={false}>
              <p className={`text-3xl font-bold ${data.summary.failed > 0 ? "text-danger-300" : "text-white"}`}>
                {data.summary.failed}
              </p>
              <p className="text-xs text-ink-300">{countLabel(data.failing.filter((t) => t.new).length, "new failure")}</p>
            </Card>
            <Card title="Flaky" copy={false}>
              <p className="text-3xl font-bold text-white">{data.flaky}</p>
              <p className="text-xs text-ink-300">passed and failed across this release&apos;s runs</p>
            </Card>
          </div>
          <Card title="Compared with the release before">
            <Delta data={data} />
          </Card>
          <Card title="Cycles">
            <ul className="divide-y divide-white/5 text-sm">
              {data.cycles.map((c) => (
                <li key={c.cycle} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2">
                  <Link to={cyclePath(c.cycle)} className="font-semibold text-brand-300 hover:underline">
                    {c.cycle}
                  </Link>
                  <span className="flex flex-wrap gap-x-3 text-xs text-ink-300">
                    <span>
                      {c.passed} passed
                      {c.failed > 0 ? <span className="text-danger-300">, {c.failed} failed</span> : null}
                      {c.flaky > 0 ? <span className="text-brand-300">, {c.flaky} flaky</span> : null}
                    </span>
                    <Link to={`/runs/${encodeURIComponent(c.run_id)}`} className="text-brand-300 hover:underline">
                      latest run {formatRunTime(c.created_at)}
                    </Link>
                    <span>{countLabel(c.runs, "run")} in this release</span>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Failing tests">
            <TestList tests={data.failing} empty="Nothing failing in this release's latest runs." />
          </Card>
          {data.fixed.length > 0 ? (
            <Card title="Fixed since the release before">
              <TestList tests={data.fixed} empty="" />
            </Card>
          ) : null}
        </div>
      )}
    </div>
  );
}
