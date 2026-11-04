import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiClient, type QuarantineItem } from "../../lib/api-client";
import { Badge } from "../../components/ui";
import { IS_STATIC_BUILD } from "../../lib/static-backend";

/** fnmatch's rules (qasight/engine/quarantine.py): `*`, `?` and `[...]`, case-sensitive. */
export function globMatches(pattern: string, name: string): boolean {
  let source = "";
  for (let i = 0; i < pattern.length; i += 1) {
    const ch = pattern[i];
    if (ch === "*") source += ".*";
    else if (ch === "?") source += ".";
    else if (ch === "[") {
      const end = pattern.indexOf("]", i + 2);
      if (end === -1) {
        source += "\\[";
      } else {
        let body = pattern.slice(i + 1, end).replace(/\\/g, "\\\\");
        if (body.startsWith("!")) body = `^${body.slice(1)}`;
        else if (body.startsWith("^")) body = `\\${body}`;
        source += `[${body}]`;
        i = end;
      }
    } else source += ch.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
  }
  return new RegExp(`^${source}$`, "s").test(name);
}

/** The stored entry that quarantines a test: an exact name first, else a glob (null if none). */
export function matchingEntry(items: QuarantineItem[], name: string, fullName = ""): QuarantineItem | null {
  const names = [name, fullName].filter(Boolean);
  return (
    items.find((item) => !item.is_glob && names.includes(item.test)) ??
    items.find((item) => item.is_glob && names.some((n) => globMatches(item.test, n))) ??
    null
  );
}

/**
 * "Quarantine" for a test, or a "Quarantined" badge and "Release" for one that is.
 *
 * A quarantined test still runs and is reported; its failure just stops failing the
 * run (`qasight run`, `qasight ingest`, `pytest --qasight`). Only an exact stored entry can be
 * released here: a glob or a qasight.yaml rule is changed where it was written.
 * The read-only demo shows the badge and no buttons.
 */
export function QuarantineControl({
  cycle,
  test,
  quarantined,
  entry,
  why
}: {
  cycle: string;
  test: string;
  quarantined: boolean;
  /** The stored entry to release, when the test is quarantined by its exact name. */
  entry: string | null;
  /** Shown on the badge: the rule's reason, or where it comes from. */
  why?: string;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const change = useMutation({
    mutationFn: (kind: "add" | "release") =>
      kind === "add"
        ? apiClient.quarantineTest({ cycle, test, reason: "Quarantined from the UI" }).then(() => undefined)
        : apiClient.releaseTest(cycle, entry ?? test),
    onMutate: () => setError(null),
    onError: (err) => setError(err instanceof Error ? err.message : "That didn't work."),
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["flaky", cycle] }),
        queryClient.invalidateQueries({ queryKey: ["quarantine", cycle] })
      ]);
    }
  });
  const canEdit = !IS_STATIC_BUILD;
  const linkClass = "-my-1 inline-block py-1 text-xs font-medium text-brand-300 hover:underline disabled:opacity-50";

  return (
    <span className="inline-flex flex-wrap items-center gap-2" data-testid="quarantine">
      {quarantined ? (
        <Badge tone="warn" title={why ?? "Its failures don't fail the run; it still runs and is reported."}>
          Quarantined
        </Badge>
      ) : null}
      {canEdit && quarantined && entry ? (
        <button
          type="button"
          className={linkClass}
          disabled={change.isPending}
          onClick={() => change.mutate("release")}
          aria-label={`Release ${test} from quarantine`}
        >
          {change.isPending ? "Releasing…" : "Release"}
        </button>
      ) : null}
      {canEdit && !quarantined ? (
        <button
          type="button"
          className={linkClass}
          disabled={change.isPending}
          onClick={() => change.mutate("add")}
          title="Keep running it, but don't let its failures fail the run"
          aria-label={`Quarantine ${test}`}
        >
          {change.isPending ? "Quarantining…" : "Quarantine"}
        </button>
      ) : null}
      {error ? (
        <span role="alert" className="text-xs text-danger-300">
          {error}
        </span>
      ) : null}
    </span>
  );
}
