/** The minimal shape every run-bearing API item shares (run list, dashboard, detail). */
export interface RunLike {
  run_id: string;
  created_at: number;
  cycle?: string | null;
  branch?: string | null;
  commit?: string | null;
}

/** The cycle a run belongs to; "run" for records that predate cycle names. */
export function runCycle(run: RunLike): string {
  return run.cycle || "run";
}

/** "Oct 7 03:21" in the viewer's locale and timezone; the year is added when it isn't the current one. */
export function formatRunTime(createdAtSeconds: number): string {
  const date = new Date(createdAtSeconds * 1000);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  const day = date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: sameYear ? undefined : "numeric" });
  const time = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  return `${day} ${time}`;
}

/**
 * How long ago, the way people say it: "just now", "4 min ago", "3 h ago",
 * "2 days ago"; older than a month reads as the date so it stays exact.
 */
export function formatRelativeTime(createdAtSeconds: number, nowMs: number = Date.now()): string {
  const seconds = Math.max(0, Math.round(nowMs / 1000 - createdAtSeconds));
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 31) return `${days} day${days === 1 ? "" : "s"} ago`;
  return formatRunTime(createdAtSeconds);
}

/** First 7 characters of a commit sha, as git itself abbreviates it. */
export function shortCommit(sha: string): string {
  return sha.slice(0, 7);
}

/** "main @ 9c23517", "main" or "9c23517": where a run came from, when it is known. */
export function formatProvenance(run: Pick<RunLike, "branch" | "commit">): string | null {
  const branch = run.branch?.trim();
  const commit = run.commit?.trim();
  if (branch && commit) return `${branch} @ ${shortCommit(commit)}`;
  if (branch) return branch;
  if (commit) return shortCommit(commit);
  return null;
}

/** First 8 characters of a run id: enough to tell runs apart, short enough to scan. */
export function shortRunId(runId: string): string {
  return runId.slice(0, 8);
}

/** How a run is named to the user: "fake-api · Oct 7 03:21". The short id is shown alongside, never instead. */
export function formatRunLabel(run: RunLike): string {
  return `${runCycle(run)} · ${formatRunTime(run.created_at)}`;
}

/** "1 regression", "3 regressions": a count with its noun in the right number. */
export function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

/** A duration the way people read it: "850 ms" under a second, "6.4 s" under a minute, then "2 min 5 s". */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "n/a";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
}

/** What an engine exit code means (Architecture Overview § Exit code contract). */
export function describeExitCode(code: number | null | undefined): string {
  switch (code) {
    case null:
    case undefined:
      return "n/a";
    case 0:
      return "0 (passed)";
    case 1:
      return "1 (tests failed)";
    case 2:
      return "2 (invalid config or input)";
    case 3:
      return "3 (infrastructure error)";
    case 4:
      return "4 (internal error)";
    default:
      return String(code);
  }
}

/** A cycle's page: `/cycles/<cycle>`. */
export function cyclePath(cycle: string): string {
  return `/cycles/${encodeURIComponent(cycle)}`;
}

/** The per-test page: `/cycles/<cycle>/test?key=<key>` (keys can hold any character). */
export function testPath(cycle: string, key: string): string {
  return `/cycles/${encodeURIComponent(cycle)}/test?${new URLSearchParams({ key }).toString()}`;
}

/**
 * A percentage as a whole number ("80%"), except that a value just under 100
 * or just over 0 keeps one decimal, so a run with one failure never reads 100%.
 */
export function formatPercent(value: number | null | undefined): string {
  if (value == null) return "n/a";
  const whole = Math.round(value);
  if (whole === 100 && value < 100) return `${Math.floor(value * 10) / 10}%`;
  if (whole === 0 && value > 0) return `${Math.ceil(value * 10) / 10}%`;
  return `${whole}%`;
}

/** "Passed 2 of the last 6 runs (33%)": a cycle's recent pass rate, from runs newest first. */
export function passRateLine(runs: { returncode?: number | null }[]): string {
  const finished = runs.filter((run) => run.returncode != null);
  if (finished.length === 0) return "No finished runs yet.";
  const passed = finished.filter((run) => run.returncode === 0).length;
  const pct = formatPercent((passed / finished.length) * 100);
  return `Passed ${passed} of the last ${countLabel(finished.length, "run")} (${pct}).`;
}

/** A short span of time for headlines: "45m", "5h", "2.5d", "12d"; "–" when unknown. Matches `delivery_metrics.span`. */
export function formatSpan(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "–";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  const days = seconds / 86400;
  return days >= 10 ? `${Math.round(days)}d` : `${days.toFixed(1)}d`;
}
