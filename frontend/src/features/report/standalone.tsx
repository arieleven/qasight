import type { ManagerReport } from "../../lib/api-client";
import { ReportDocument } from "./ReportDocument";

/**
 * A report baked into the page by `testo export` (single-file HTML, PDF, PNG):
 * `window.__TESTO_REPORT__` holds the payload, so nothing is fetched and no
 * router or server is needed. `?capture=1` hides the Copy menus for images.
 */
export interface EmbeddedReport {
  report: ManagerReport;
  brand?: string;
}

declare global {
  interface Window {
    __TESTO_REPORT__?: EmbeddedReport;
  }
}

export function embeddedReport(): EmbeddedReport | null {
  return typeof window !== "undefined" && window.__TESTO_REPORT__ ? window.__TESTO_REPORT__ : null;
}

export function StandaloneReport({ embedded }: { embedded: EmbeddedReport }) {
  const capture = new URLSearchParams(window.location.search).has("capture");
  document.title = `${embedded.report.title} · ${embedded.brand ?? "Testosterone"}`;
  document.body.classList.add("report-standalone");
  return (
    <main className="px-3 py-6 sm:px-6 sm:py-10 print:p-0">
      <ReportDocument report={embedded.report} brand={embedded.brand} copy={!capture} />
    </main>
  );
}
