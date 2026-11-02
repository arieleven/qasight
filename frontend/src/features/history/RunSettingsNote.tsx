import { useQuery } from "@tanstack/react-query";

import { apiClient, type RunSettings } from "../../lib/api-client";
import { IS_STATIC_BUILD } from "../../lib/static-backend";

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "Runs older than 30 days or beyond the newest 50 of a cycle are deleted automatically every 6 hours." */
export function retentionLine(settings: RunSettings): string {
  const { days, runs_per_cycle: perCycle, enabled } = settings.retention;
  if (!enabled && !settings.config_found) {
    return "No testosterone.yaml or TESTO_RETENTION_DAYS here, so no run is deleted automatically.";
  }
  if (!enabled) return "Runs are kept until you delete them: no history.retention or TESTO_RETENTION_DAYS is set.";
  const limits = [
    days != null ? `older than ${plural(days, "day")}` : null,
    perCycle != null ? `beyond the newest ${perCycle} of a cycle` : null
  ].filter(Boolean);
  const every = settings.cleanup_interval_hours ? ` every ${plural(settings.cleanup_interval_hours, "hour")}` : "";
  return `Runs ${limits.join(" or ")} are deleted automatically${every}.`;
}

export function timeoutLine(settings: RunSettings): string | null {
  if (!settings.config_found) return null;
  const limit = settings.default_stage_timeout_s;
  return limit == null
    ? "Stages have no time limit by default (timeout_s: null)."
    : `A stage is stopped after ${limit} s unless it sets its own timeout_s.`;
}

/** How long runs are kept and how long a stage may take, read from testosterone.yaml. */
export function RunSettingsNote() {
  const query = useQuery({
    queryKey: ["run-settings"],
    queryFn: () => apiClient.getRunSettings(),
    // The demo snapshot has no config to read.
    enabled: !IS_STATIC_BUILD
  });
  if (!query.data?.retention) return null;
  const timeout = timeoutLine(query.data);
  return (
    <div className="mt-6 rounded-2xl border border-white/5 bg-ink-950/60 px-4 py-3 text-xs text-ink-300" data-testid="run-settings">
      <p className="font-semibold text-ink-200">Keeping runs</p>
      <p className="mt-1">{retentionLine(query.data)}</p>
      {timeout ? <p className="mt-1">{timeout}</p> : null}
      {query.data.config_found ? (
        <p className="mt-1 text-ink-400">
          Change these in testosterone.yaml (<code>history.retention</code>, <code>timeout_s</code>), then restart the
          server.
        </p>
      ) : null}
    </div>
  );
}
