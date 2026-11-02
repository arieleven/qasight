import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { apiClient, type CycleExecutionAccepted } from "../../lib/api-client";
import { subscribeToCycleExecutionEvents, type CycleNdjsonEvent } from "../../lib/sse-client";

export type RunPhase = "idle" | "starting" | "running" | "passed" | "failed" | "aborted" | "stopped";

export type StageRow = {
  stage: string;
  framework?: string;
  index?: number;
  returncode?: number;
  duration_s?: number;
  status: "pending" | "running" | "completed";
};

/**
 * Live state of one engine execution (a named cycle or an ad-hoc run).
 *
 * `start` takes the POST that creates the execution; everything after that is
 * shared: tail `/cycle-executions/{id}/events` over SSE and fold the NDJSON
 * events into a phase, a stage timeline and the raw event list.
 */
export function useCycleExecution() {
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [events, setEvents] = useState<CycleNdjsonEvent[]>([]);
  const [stages, setStages] = useState<Record<string, StageRow>>({});
  // Asked to stop (the Stop button); the run ends when its stages have.
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState<string | null>(null);
  const unsubscribeRef = useRef<null | (() => void)>(null);

  useEffect(() => () => unsubscribeRef.current?.(), []);

  // A finished run changes every list on screen (the cycle's recent runs, the
  // runs page, the dashboard). The run is recorded just after the engine's last
  // event, so refetch now and once more a moment later.
  const queryClient = useQueryClient();
  const finished = phase === "passed" || phase === "failed" || phase === "aborted" || phase === "stopped";
  useEffect(() => {
    if (!finished) return;
    void queryClient.invalidateQueries();
    const later = setTimeout(() => void queryClient.invalidateQueries(), 2000);
    return () => clearTimeout(later);
  }, [finished, queryClient]);

  function stopStream() {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
  }

  function applyEvent(evt: CycleNdjsonEvent) {
    setEvents((prev) => [...prev, evt]);
    if (evt.event === "plan_started") {
      setPhase("running");
    }
    if (evt.event === "stage_started") {
      setStages((prev) => ({
        ...prev,
        [evt.stage]: { stage: evt.stage, framework: evt.framework, index: evt.index, status: "running" }
      }));
    }
    if (evt.event === "stage_finished") {
      setStages((prev) => ({
        ...prev,
        [evt.stage]: {
          ...(prev[evt.stage] ?? { stage: evt.stage, status: "pending" }),
          status: "completed",
          returncode: evt.returncode,
          duration_s: evt.duration_s
        }
      }));
    }
    if (evt.event === "plan_aborted") {
      setPhase(evt.reason === "fail_fast" ? "aborted" : "stopped");
      stopStream();
    }
    if (evt.event === "plan_finished") {
      setExitCode(evt.exit_code);
      setPhase(evt.exit_code === 0 ? "passed" : "failed");
      stopStream();
    }
    if (evt.event === "error") {
      setErrorMessage(evt.message);
      setPhase(evt.code === "cancelled" ? "stopped" : "failed");
      stopStream();
    }
  }

  async function resolvePhaseFromStatus(id: string) {
    try {
      const status = await apiClient.getCycleExecutionStatus(id);
      setPhase((current) => {
        if (current !== "running" && current !== "starting") return current;
        if (status.status === "completed") return "passed";
        if (status.status === "failed") return "failed";
        if (status.status === "cancelled") return "stopped";
        return current;
      });
      if (status.error) setErrorMessage(status.error);
    } catch {
      setPhase((current) => (current === "running" || current === "starting" ? "failed" : current));
    }
  }

  async function start(create: () => Promise<CycleExecutionAccepted>) {
    setPhase("starting");
    setErrorMessage(null);
    setExitCode(null);
    setEvents([]);
    setStages({});
    setExecutionId(null);
    setStopping(false);
    setStopError(null);
    stopStream();

    try {
      const created = await create();
      setExecutionId(created.execution_id);
      setPhase("running");
      unsubscribeRef.current = subscribeToCycleExecutionEvents(created.events_url, {
        onEvent: applyEvent,
        onError: () => {
          stopStream();
          // EventSource fires error on normal server close too. Rather than assume
          // failure, poll the execution's own status endpoint for the real outcome.
          void resolvePhaseFromStatus(created.execution_id);
        }
      });
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : String(err));
      setPhase("failed");
    }
  }

  /** Stop the run (again: kill stages that ignore the first stop). */
  async function stop() {
    if (!executionId) return;
    setStopping(true);
    setStopError(null);
    try {
      const status = await apiClient.cancelCycleExecution(executionId);
      if (status.status === "cancelled") {
        // Stopped while queued: no event will come, so finish here.
        setPhase("stopped");
        if (status.error) setErrorMessage(status.error);
        stopStream();
      }
    } catch (err) {
      setStopping(false);
      setStopError(err instanceof Error ? err.message : String(err));
    }
  }

  const stageRows = useMemo(
    () => Object.values(stages).sort((a, b) => (a.index ?? 0) - (b.index ?? 0)),
    [stages]
  );

  return {
    phase,
    executionId,
    errorMessage,
    exitCode,
    events,
    stageRows,
    busy: phase === "starting" || phase === "running",
    finished,
    stopping,
    stopError,
    start,
    stop
  };
}

export type CycleExecution = ReturnType<typeof useCycleExecution>;
