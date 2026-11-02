import { useEffect, useRef, useState } from "react";

import { Button } from "../../components/ui";
import { apiClient, type ReportFormat, type ReportPeriod } from "../../lib/api-client";
import { downloadBlob } from "../../lib/clipboard";

const FORMATS: { format: ReportFormat; label: string; note: string }[] = [
  { format: "pdf", label: "PDF", note: "A4, ready to email or print" },
  { format: "pptx", label: "PowerPoint", note: "Slides with editable charts" },
  { format: "png", label: "Image (PNG)", note: "The whole report as one picture" },
  { format: "xlsx", label: "Excel", note: "Every table, plus a trend chart" },
  { format: "html", label: "Web page (HTML)", note: "One file that opens offline" },
  { format: "csv", label: "CSV", note: "The cycles table" },
  { format: "json", label: "JSON", note: "The raw numbers" }
];

/**
 * Download the report as a file the server renders (`GET /api/v1/report/export`,
 * the same as `testo export`). Not in the static demo, which has no server.
 */
export function DownloadMenu({ period, cycle }: { period: ReportPeriod; cycle: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<ReportFormat | null>(null);
  const [error, setError] = useState("");
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const download = async (format: ReportFormat) => {
    setOpen(false);
    setBusy(format);
    setError("");
    try {
      const { blob, filename } = await apiClient.downloadReport(format, period, cycle || undefined);
      downloadBlob(filename ?? `test-report.${format}`, blob);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div ref={wrap} className="relative">
      <Button variant="secondary" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)} disabled={busy !== null}>
        {busy ? `Preparing ${busy.toUpperCase()}…` : "Download"}
      </Button>
      {open ? (
        <div role="menu" aria-label="Download the report" className="absolute right-0 top-full z-30 mt-1 w-64 rounded-xl border border-white/10 bg-ink-900 p-1 shadow-xl">
          {FORMATS.map((f) => (
            <button
              key={f.format}
              type="button"
              role="menuitem"
              onClick={() => void download(f.format)}
              className="block w-full rounded-lg px-3 py-1.5 text-left hover:bg-ink-800"
            >
              <span className="block text-sm text-ink-100">{f.label}</span>
              <span className="block text-xs text-ink-300">{f.note}</span>
            </button>
          ))}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="absolute right-0 top-full z-20 mt-1 w-80 rounded-xl border border-danger-500/40 bg-ink-900 p-3 text-xs text-danger-300">
          {error}
        </p>
      ) : null}
    </div>
  );
}
