export type CycleNdjsonEvent =
  | { event: "plan_started"; plan: string; stage_count: number }
  | { event: "stage_started"; stage: string; framework: string; index: number; count: number }
  | {
      event: "stage_finished";
      stage: string;
      framework: string;
      returncode: number;
      duration_s: number;
      log_path: string | null;
      timed_out: boolean;
      internal_failure?: boolean;
      error?: string | null;
    }
  | {
      event: "plan_aborted";
      plan: string;
      /** fail_fast; stopped (the Stop button / API); signal (Ctrl-C, SIGTERM) */
      reason: "fail_fast" | "stopped" | "signal";
      completed_stages: number;
      signal?: string;
    }
  | {
      event: "plan_finished";
      plan: string;
      aggregate_returncode: number;
      exit_code: number;
      duration_s: number;
      stages?: unknown[];
      error?: string | null;
    }
  | { event: "cycle_trigger"; cycle: string; status: "activated" | "resting"; reason: string; matched: string[]; mode: string }
  | { event: "error"; code: string; message: string };

export function subscribeToCycleExecutionEvents(
  eventsUrl: string,
  handlers: {
    onEvent: (event: CycleNdjsonEvent) => void;
    onError?: (error: Event) => void;
  }
): () => void {
  const source = new EventSource(eventsUrl);

  const handle = (evt: MessageEvent) => {
    handlers.onEvent(JSON.parse(evt.data) as CycleNdjsonEvent);
  };

  source.addEventListener("plan_started", handle);
  source.addEventListener("stage_started", handle);
  source.addEventListener("stage_finished", handle);
  source.addEventListener("plan_aborted", handle);
  source.addEventListener("plan_finished", handle);
  source.addEventListener("cycle_trigger", handle);
  source.addEventListener("error", handle);

  source.onerror = (evt) => {
    handlers.onError?.(evt);
  };
  return () => source.close();
}
