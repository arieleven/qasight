import { FormEvent, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiClient, type CycleExecutionRequest } from "../../lib/api-client";
import { Button, Card, Spinner } from "../../components/ui";
import { IS_STATIC_BUILD } from "../../lib/static-backend";
import { ExecutionProgress } from "../execution/ExecutionProgress";
import { useCycleExecution } from "../execution/useCycleExecution";

const REPORTERS = ["allure", "extent", "reportportal", "testbeats"] as const;

export interface RunPanelProps {
  /** Pre-selected cycle; when set the dropdown starts on it. */
  initialCycle?: string;
  /** Hide the cycle selector entirely (cycle detail page context). */
  lockCycle?: boolean;
  /** Open the panel ready-to-run (e.g. arriving from a card's Run button). */
  autoFocusRun?: boolean;
  /** Run only some tests: these names, or the failing tests of a recorded run. */
  selection?: RunSelection | null;
  /** Called when the person chooses to run every test instead of the selection. */
  onClearSelection?: () => void;
}

export interface RunSelection {
  tests?: string[];
  rerunFailed?: string;
  /** How the panel names it: "the 3 failing tests of run 1a2b3c4d". */
  label: string;
}

export function RunPanel({ initialCycle, lockCycle = false, selection, onClearSelection }: RunPanelProps) {
  const cyclesQuery = useQuery({
    queryKey: ["cycles"],
    queryFn: () => apiClient.listCycles(),
    enabled: !lockCycle
  });
  // Recorded-only cycles (qasight ingest) have no config to run.
  const runnable = (cyclesQuery.data?.items ?? []).filter((item) => item.source === "config");

  const [cycle, setCycle] = useState(initialCycle ?? "");
  const [stream, setStream] = useState(true);
  const [persist, setPersist] = useState(true);
  const [failFast, setFailFast] = useState(false);
  const [force, setForce] = useState(false);

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [workersOverride, setWorkersOverride] = useState("");
  const [reporters, setReporters] = useState<string[]>([]);
  const [reportDb, setReportDb] = useState(true);
  const [asyncReportDb, setAsyncReportDb] = useState(false);
  const [configPath, setConfigPath] = useState("");
  const [artifactsRoot, setArtifactsRoot] = useState("");

  const execution = useCycleExecution();
  const { busy } = execution;

  // Default the dropdown to the first cycle once loaded.
  useEffect(() => {
    if (!cycle && runnable.length) {
      setCycle(initialCycle ?? runnable[0].name);
    }
  }, [cycle, runnable, initialCycle]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!cycle) return;

    const payload: CycleExecutionRequest = {
      stream,
      persist,
      fail_fast: failFast,
      force,
      report_db: reportDb,
      async_report_db: asyncReportDb
    };
    const workers = Number.parseInt(workersOverride, 10);
    if (Number.isFinite(workers) && workers > 0) payload.workers_override = workers;
    if (reporters.length > 0) payload.reporter_override = reporters;
    if (configPath.trim()) payload.config_path = configPath.trim();
    if (artifactsRoot.trim()) payload.artifacts_root = artifactsRoot.trim();
    if (selection?.rerunFailed) payload.rerun_failed = selection.rerunFailed;
    else if (selection?.tests?.length) payload.tests = selection.tests;

    await execution.start(() => apiClient.createCycleExecution(cycle, payload));
  }

  return (
    <div className="space-y-4">
      <Card title={selection ? "Run chosen tests" : lockCycle ? "Run this cycle" : "Run a cycle"}>
        <form onSubmit={onSubmit} className="space-y-4" data-testid="run-panel-form">
          {selection ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-brand-600/40 bg-brand-600/10 px-3 py-2 text-sm text-ink-100">
              <span>Runs only {selection.label}.</span>
              {onClearSelection ? (
                <button
                  type="button"
                  onClick={onClearSelection}
                  className="text-xs text-brand-300 hover:underline"
                  disabled={busy}
                >
                  Run every test instead
                </button>
              ) : null}
            </div>
          ) : null}
          {!lockCycle ? (
            <label className="grid max-w-sm gap-1">
              <span className="text-xs font-medium text-ink-300">Cycle</span>
              <select
                className="rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 focus:border-brand-500 focus:outline-none text-sm text-ink-100"
                value={cycle}
                onChange={(ev) => setCycle(ev.target.value)}
                disabled={cyclesQuery.isLoading || busy}
              >
                {cyclesQuery.isLoading ? <option value="">Loading cycles…</option> : null}
                {runnable.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name} — {item.stage_count} stage{item.stage_count === 1 ? "" : "s"}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          <div className="flex flex-wrap gap-4">
            <Toggle label="Live log stream" checked={stream} onChange={setStream} disabled={busy} />
            <Toggle label="Persist run" checked={persist} onChange={setPersist} disabled={busy} />
            <Toggle label="Fail fast" checked={failFast} onChange={setFailFast} disabled={busy} />
            <Toggle label="Force (ignore triggers)" checked={force} onChange={setForce} disabled={busy} />
          </div>

          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="text-xs font-medium text-brand-300 hover:text-brand-400"
              aria-expanded={showAdvanced}
            >
              {showAdvanced ? "▾ Hide advanced options" : "▸ Advanced options"}
            </button>
            {showAdvanced ? (
              <div className="mt-3 grid gap-4 rounded-2xl border border-white/5 bg-ink-950/70/60 p-4 sm:grid-cols-2">
                <label className="grid gap-1">
                  <span className="text-xs font-medium text-ink-300">Workers override</span>
                  <input
                    type="number"
                    min={1}
                    className="rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 focus:border-brand-500 focus:outline-none text-sm text-ink-100"
                    value={workersOverride}
                    onChange={(ev) => setWorkersOverride(ev.target.value)}
                    placeholder="engine default"
                    disabled={busy}
                  />
                </label>
                <div className="grid gap-1">
                  <span className="text-xs font-medium text-ink-300">Reporters override</span>
                  <div className="flex flex-wrap gap-3 pt-1.5">
                    {REPORTERS.map((rep) => (
                      <Toggle
                        key={rep}
                        label={rep}
                        checked={reporters.includes(rep)}
                        onChange={(checked) =>
                          setReporters((prev) => (checked ? [...prev, rep] : prev.filter((r) => r !== rep)))
                        }
                        disabled={busy}
                      />
                    ))}
                  </div>
                </div>
                <Toggle label="Archive to report DB" checked={reportDb} onChange={setReportDb} disabled={busy} />
                <Toggle
                  label="Archive in background"
                  checked={asyncReportDb}
                  onChange={setAsyncReportDb}
                  disabled={busy || !reportDb}
                />
                <label className="grid gap-1">
                  <span className="text-xs font-medium text-ink-300">Config path</span>
                  <input
                    className="rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 focus:border-brand-500 focus:outline-none font-mono text-sm text-ink-100"
                    value={configPath}
                    onChange={(ev) => setConfigPath(ev.target.value)}
                    placeholder="qasight.yaml (auto-discover)"
                    disabled={busy}
                  />
                </label>
                <label className="grid gap-1">
                  <span className="text-xs font-medium text-ink-300">Artifacts root</span>
                  <input
                    className="rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 focus:border-brand-500 focus:outline-none font-mono text-sm text-ink-100"
                    value={artifactsRoot}
                    onChange={(ev) => setArtifactsRoot(ev.target.value)}
                    placeholder="artifacts/"
                    disabled={busy}
                  />
                </label>
              </div>
            ) : null}
          </div>

          <div className="flex items-center gap-3">
            <Button type="submit" disabled={busy || !cycle || IS_STATIC_BUILD}>
              {busy ? (
                <>
                  <Spinner className="h-3.5 w-3.5 border-white/40 border-t-white" /> Running…
                </>
              ) : (
                "Run cycle"
              )}
            </Button>
            {IS_STATIC_BUILD && cycle ? (
              <span className="text-xs text-ink-400">
                Read-only demo. Run locally with{" "}
                <code className="font-mono text-ink-200">qasight run --cycle {cycle}</code>
              </span>
            ) : null}
            {execution.executionId ? (
              <span className="font-mono text-xs text-ink-400">execution {execution.executionId}</span>
            ) : null}
          </div>
        </form>
      </Card>

      <ExecutionProgress execution={execution} noun="Cycle" persisted={persist} />
    </div>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
  disabled
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-center gap-2 text-sm ${disabled ? "text-ink-500" : "text-ink-200"}`}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(ev) => onChange(ev.target.checked)}
        disabled={disabled}
        className="h-4 w-4 rounded border-ink-600 bg-ink-950 accent-brand-500"
      />
      {label}
    </label>
  );
}
