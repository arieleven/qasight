import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import {
  apiClient,
  type ReleaseDetailResponse,
  type ReleaseSummaryItem,
  type ReleaseTestItem
} from "../../lib/api-client";
import { Badge, Card, EmptyState, PageHeader, ShowMoreButton, Spinner, useShowMore } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { DemoExampleNote, useIsDemoExample } from "../../components/DemoExampleNote";
import { countLabel, cyclePath, formatPercent, formatRunTime, testPath } from "../../lib/format";
import { ReleasesTabs } from "./ReleasesTabs";

export function releasePath(release: string): string {
  return `/releases/${encodeURIComponent(release)}`;
}

function HowTo() {
  return (
    <>
        Record the release a run tests with <code>qasight run --release 2.3</code> (or <code>qasight ingest --release</code>, the{" "}
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
      <PageHeader title="Releases" subtitle="Each release in one place: what its tests did, and how its code scores." />
      <ReleasesTabs />
      <ExampleNote />
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
          <ul className="divide-fade">
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

const RELEASE_NOTE =
  "These releases are made up: the demo's runs were grouped into two pretend releases to show the page. Record real ones with qasight run --release.";

function ExampleNote() {
  return useIsDemoExample("releases") ? <DemoExampleNote className="mb-4" what={RELEASE_NOTE} /> : null;
}

function TestList({ tests, empty }: { tests: ReleaseTestItem[]; empty: string }) {
  const rows = useShowMore(tests);
  if (tests.length === 0) return <p className="text-sm text-ink-300">{empty}</p>;
  return (
    <>
      <ul className="space-y-2 text-sm">
        {rows.visible.map((test) => (
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
      <ShowMoreButton className="mt-3" total={rows.total} expanded={rows.expanded} onToggle={rows.setExpanded} />
    </>
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

/** The release's code grade from the scorecard, when a scan recorded this version. */
function CodeQualityTile({ release }: { release: string }) {
  const query = useQuery({ queryKey: ["scorecard", "", release], queryFn: () => apiClient.getScorecard(undefined, release) });
  const card = query.data;
  const scored = card?.status === "available" && card.version === release && card.score != null;
  const href = `/code-quality?${new URLSearchParams({ ...(card?.project ? { project: card.project } : {}), version: release })}`;
  return (
    <Card title="Code quality" copy={false}>
      {scored ? (
        <>
          <p className="text-3xl font-bold text-white">
            {card.grade ?? ""} <span className="tabular-nums">{card.score}</span>
            <span className="text-sm font-normal text-ink-400"> / 100</span>
          </p>
          <Link to={href} className="text-xs text-brand-300 hover:underline">
            See what drives it
          </Link>
        </>
      ) : (
        <>
          <p className="text-3xl font-bold text-ink-400">n/a</p>
          <p className="text-xs text-ink-300">
            No code scan for {release}. Run <code className="font-mono">qasight scorecard scan . --version {release}</code>.
          </p>
        </>
      )}
    </Card>
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
      <ExampleNote />
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
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
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
            <CodeQualityTile release={release} />
          </div>
          <Card title="Compared with the release before">
            <Delta data={data} />
          </Card>
          <Card title="Cycles">
            <ul className="divide-fade text-sm">
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
