import { useId, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { apiClient, type FailingTestItem, type TriageCause, type TriageItem } from "../../lib/api-client";
import { Badge, Button } from "../../components/ui";
import { formatRunTime, shortRunId } from "../../lib/format";
import { IS_STATIC_BUILD } from "../../lib/static-backend";

// Same order and wording as qasight/services/triage.py CAUSES.
export const CAUSE_LABELS: Record<TriageCause, string> = {
  product_bug: "Bug in the product",
  test_bug: "Bug in the test",
  environment: "Environment or infrastructure",
  flaky: "Flaky test",
  other: "Other"
};

const inputClass =
  "rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 text-sm text-ink-100 focus:border-brand-500 focus:outline-none";

/** Where a cause came from: this failure, or an earlier one with the same message. */
function origin(triage: TriageItem, runId: string, testKey: string): string {
  const who = triage.marked_by ? ` by ${triage.marked_by}` : "";
  const when = formatRunTime(triage.marked_at);
  if (triage.run_id === runId && triage.test_key === testKey) return `Marked${who} on ${when}`;
  const where = triage.run_id ? ` in run ${shortRunId(triage.run_id)}` : "";
  return `From an earlier failure with the same message${where}, marked${who} on ${when}`;
}

function CauseForm({
  initial,
  onCancel,
  onSave
}: {
  initial: TriageItem | null;
  onCancel: () => void;
  onSave: (cause: TriageCause, note: string) => Promise<void>;
}) {
  const id = useId();
  const [cause, setCause] = useState<TriageCause>(initial?.cause ?? "product_bug");
  const [note, setNote] = useState(initial?.note ?? "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSave(cause, note);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't save the cause.");
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-2 grid gap-2 rounded-xl border border-white/10 bg-ink-900/60 p-3" aria-label="Mark the cause">
      <div className="flex flex-wrap gap-2">
        <div className="grid gap-1">
          <label className="text-xs font-medium text-ink-300" htmlFor={`${id}-cause`}>
            Cause
          </label>
          <select
            id={`${id}-cause`}
            className={inputClass}
            value={cause}
            onChange={(event) => setCause(event.target.value as TriageCause)}
          >
            {Object.entries(CAUSE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="grid min-w-0 flex-1 gap-1">
          <label className="text-xs font-medium text-ink-300" htmlFor={`${id}-note`}>
            Note (optional)
          </label>
          <input
            id={`${id}-note`}
            className={inputClass}
            value={note}
            maxLength={500}
            placeholder="Issue link, who's on it…"
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
      </div>
      <p className="text-xs text-ink-400">Later failures in this cycle with the same message get this cause too.</p>
      {error ? (
        <p role="alert" className="text-xs text-danger-300">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saving}>
          {saving ? "Saving…" : "Save cause"}
        </Button>
        <Button type="button" variant="secondary" size="sm" onClick={onCancel} disabled={saving}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/**
 * The cause of one failing test: shown when someone marked it (on this failure or
 * an earlier one with the same message), and editable on a live server.
 */
export function TriageControl({ test, runId, cycle }: { test: FailingTestItem; runId: string; cycle?: string | null }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const triage = test.triage ?? null;
  const canEdit = !IS_STATIC_BUILD && Boolean(cycle) && Boolean(test.message);

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["run-failures", runId] });
  }

  async function save(cause: TriageCause, note: string) {
    await apiClient.markTriage({
      cycle: cycle as string,
      message: test.message,
      cause,
      note,
      run_id: runId,
      test_key: test.key
    });
    setEditing(false);
    await refresh();
  }

  async function clear() {
    if (!triage) return;
    setError(null);
    try {
      await apiClient.clearTriage(triage.cycle, triage.signature);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't clear the cause.");
    }
  }

  if (editing) {
    return <CauseForm initial={triage} onCancel={() => setEditing(false)} onSave={save} />;
  }
  if (!triage) {
    return canEdit ? (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="-my-1 inline-block py-1 text-xs font-medium text-brand-300 hover:underline"
      >
        Mark the cause
      </button>
    ) : null;
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="triage">
      <Badge tone="brand">Cause: {CAUSE_LABELS[triage.cause]}</Badge>
      {triage.note ? <span className="break-words text-ink-200">{triage.note}</span> : null}
      <span className="text-ink-400">{origin(triage, runId, test.key)}</span>
      {canEdit ? (
        <>
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="-my-1 inline-block py-1 font-medium text-brand-300 hover:underline"
          >
            Change
          </button>
          <button
            type="button"
            onClick={clear}
            className="-my-1 inline-block py-1 font-medium text-brand-300 hover:underline"
          >
            Clear
          </button>
        </>
      ) : null}
      {error ? (
        <span role="alert" className="text-danger-300">
          {error}
        </span>
      ) : null}
    </div>
  );
}
