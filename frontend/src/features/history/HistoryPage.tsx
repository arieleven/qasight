import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient, type DeleteRunsResponse } from "../../lib/api-client";
import { IS_STATIC_BUILD } from "../../lib/static-backend";
import { formatPercent, passRateLine } from "../../lib/format";
import { Button, EmptyState, PageHeader, RunLabel, Spinner, StatusPill, buttonClasses } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { RunSettingsNote } from "./RunSettingsNote";
import { StorageNote } from "./StorageNote";

const selectClass =
  "rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 text-sm text-ink-100 focus:border-brand-500 focus:outline-none";

const PAGE = 30;

function isRunning(status: string | null | undefined): boolean {
  return (status ?? "").toUpperCase() === "RUNNING";
}

function deletedLine(result: DeleteRunsResponse): string {
  const n = result.deleted.length;
  const parts = [`Deleted ${n} run${n === 1 ? "" : "s"}.`];
  if (result.skipped_running.length) parts.push(`${result.skipped_running.length} still running, left alone.`);
  if (result.not_found.length) parts.push(`${result.not_found.length} were already gone.`);
  if (result.errors.length) parts.push(`Some report files could not be removed: ${result.errors.join("; ")}`);
  return parts.join(" ");
}

export function HistoryPage() {
  const [params, setParams] = useSearchParams();
  const cycle = params.get("cycle") ?? "";
  const [limit, setLimit] = useState(PAGE);
  const runsQuery = useQuery({
    queryKey: ["runs", cycle, limit],
    queryFn: () => apiClient.listRuns(cycle || undefined, limit),
    // Keep the list on screen while the next page loads.
    placeholderData: keepPreviousData
  });
  const cyclesQuery = useQuery({ queryKey: ["cycles"], queryFn: () => apiClient.listCycles() });
  const cycleNames = (cyclesQuery.data?.items ?? []).map((item) => item.name);

  // Choosing runs to delete: off until "Select runs", then a checkbox per finished run.
  const [selecting, setSelecting] = useState(false);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const deletion = useMutation({
    mutationFn: (runIds: string[]) => apiClient.deleteRuns(runIds),
    onSuccess: (result) => {
      setNotice(deletedLine(result));
      setChosen(new Set());
      setConfirming(false);
      setSelecting(false);
      // Every list that showed those runs (dashboard, cycle pages, compare) is stale now.
      void queryClient.invalidateQueries();
    }
  });

  function toggle(runId: string) {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
    setConfirming(false);
  }

  function startSelecting() {
    setSelecting(true);
    setNotice(null);
  }

  function stopSelecting() {
    setSelecting(false);
    setChosen(new Set());
    setConfirming(false);
    deletion.reset();
  }

  function pickCycle(name: string) {
    setParams(name ? { cycle: name } : {}, { replace: true });
    setLimit(PAGE);
  }

  if (runsQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-ink-300">
        <Spinner /> Loading run history...
      </div>
    );
  }
  if (runsQuery.isError) {
    return <p className="text-sm text-danger-400">Couldn't load runs. Check that the API is running, then reload.</p>;
  }

  const items = runsQuery.data?.items ?? [];
  // Baseline must come from the same cycle; comparing different cycles reports every test as removed.
  const latest = items[0];
  const baseline = latest?.cycle ? items.slice(1).find((run) => run.cycle === latest.cycle) : undefined;

  return (
    <section>
      <PageHeader
        title="Runs"
        subtitle={cycle ? `Runs of ${cycle}, newest first.` : "Every recorded run, newest first."}
        actions={
          <>
            {latest && baseline ? (
              <Link
                to={`/compare?current_run_id=${latest.run_id}&baseline_run_id=${baseline.run_id}`}
                className={buttonClasses("secondary", "sm")}
              >
                Compare latest two
              </Link>
            ) : (
              <Link to="/compare" className={buttonClasses("secondary", "sm")}>
                Compare runs
              </Link>
            )}
            {!IS_STATIC_BUILD && items.length > 0 ? (
              <Button
                variant="secondary"
                size="sm"
                aria-pressed={selecting}
                onClick={selecting ? stopSelecting : startSelecting}
              >
                {selecting ? "Done" : "Select runs"}
              </Button>
            ) : null}
          </>
        }
      />
      {notice ? (
        <p role="status" className="mb-3 text-sm text-ink-200" data-testid="delete-notice">
          {notice}
        </p>
      ) : null}
      {selecting ? (
        <div
          className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-white/10 bg-ink-900/70 px-3 py-2 text-sm"
          data-testid="delete-toolbar"
        >
          <span className="text-ink-200">
            {chosen.size === 0 ? "Pick the runs to delete." : `${chosen.size} run${chosen.size === 1 ? "" : "s"} selected.`}
          </span>
          {confirming ? (
            <>
              <span className="text-danger-400">
                Delete {chosen.size === 1 ? "it" : "them"} for good? Their results and report copies go too.
              </span>
              <Button
                variant="danger"
                size="sm"
                disabled={deletion.isPending}
                onClick={() => deletion.mutate([...chosen])}
              >
                {deletion.isPending ? "Deleting…" : "Yes, delete"}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirming(false)} disabled={deletion.isPending}>
                Keep them
              </Button>
            </>
          ) : (
            <Button variant="danger" size="sm" disabled={chosen.size === 0} onClick={() => setConfirming(true)}>
              Delete selected
            </Button>
          )}
          {deletion.isError ? (
            <span role="alert" className="text-danger-400">
              Couldn't delete: {deletion.error instanceof Error ? deletion.error.message : String(deletion.error)}
            </span>
          ) : null}
        </div>
      ) : null}
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="grid gap-1">
          <label htmlFor="runs-cycle" className="text-xs font-medium text-ink-300">
            Cycle
          </label>
          <select id="runs-cycle" className={selectClass} value={cycle} onChange={(event) => pickCycle(event.target.value)}>
            <option value="">All cycles</option>
            {[...new Set([...cycleNames, ...(cycle ? [cycle] : [])])].map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        {cycle && items.length > 0 ? (
          <p className="pb-2 text-sm text-ink-200" data-testid="pass-rate">
            {passRateLine(items)}
          </p>
        ) : null}
      </div>
      {items.length === 0 && cycle ? (
        <p className="text-sm text-ink-400">No recorded runs of {cycle} yet.</p>
      ) : items.length === 0 ? (
        <EmptyState
          mascot={<MuscleShrug />}
          title="No runs yet"
          message="Run a cycle or record a report with qasight ingest, and it will show up here."
          action={
            <Link to="/cycles" className={buttonClasses("primary", "sm")}>
              Browse cycles
            </Link>
          }
        />
      ) : (
        <ul className="divide-fade glass-card">
          {items.map((run) => (
            <li key={run.run_id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
              <div className="flex min-w-0 items-center gap-3">
                {selecting ? (
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-brand-500"
                    aria-label={`Select run ${run.run_id}`}
                    checked={chosen.has(run.run_id)}
                    disabled={isRunning(run.status)}
                    title={isRunning(run.status) ? "Still running: stop it first." : undefined}
                    onChange={() => toggle(run.run_id)}
                  />
                ) : null}
                <StatusPill status={run.status ?? "unknown"} returncode={run.returncode} />
                <RunLabel run={run} linked relative />
              </div>
              <span className="text-xs text-ink-400">
                {run.health_pct != null && run.total_tests ? `${formatPercent(run.health_pct)} passed` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      {items.length >= limit ? (
        <button
          type="button"
          className={`mt-3 ${buttonClasses("secondary", "sm")}`}
          disabled={runsQuery.isFetching}
          onClick={() => setLimit((current) => current + PAGE)}
        >
          {runsQuery.isFetching ? "Loading…" : "Show older runs"}
        </button>
      ) : null}
      <RunSettingsNote />
      <StorageNote />
    </section>
  );
}
