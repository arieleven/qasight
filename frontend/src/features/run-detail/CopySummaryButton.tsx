import { useState } from "react";

import { Button } from "../../components/ui";
import { apiClient } from "../../lib/api-client";
import { copyText } from "../../lib/clipboard";

/**
 * "Copy summary": the run as Markdown (the `testo report markdown` text), ready to
 * paste into a pull request, Confluence, Notion, Jira or a chat message.
 */
export function CopySummaryButton({ runId }: { runId: string }) {
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const copy = async () => {
    setBusy(true);
    try {
      const { markdown } = await apiClient.getRunMarkdown(runId);
      setStatus((await copyText(markdown)) ? "Summary copied as Markdown" : "Your browser blocked the clipboard.");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Could not load the summary.");
    } finally {
      setBusy(false);
      setTimeout(() => setStatus(""), 3000);
    }
  };

  return (
    <span className="inline-flex items-center gap-2 print:hidden">
      <span aria-live="polite" className="text-xs text-ink-300">
        {status}
      </span>
      <Button variant="secondary" size="sm" onClick={() => void copy()} disabled={busy} title="Copy this run as Markdown">
        Copy summary
      </Button>
    </span>
  );
}
