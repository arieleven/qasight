import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { Button, EmptyState, PageHeader, Spinner } from "../../components/ui";
import { apiClient, type ReportPeriod } from "../../lib/api-client";
import { IS_STATIC_BUILD } from "../../lib/static-backend";
import { CyclePicker } from "../health/ProjectHealthPage";
import { DownloadMenu } from "./DownloadMenu";
import { ReportDocument } from "./ReportDocument";

const PERIODS: { value: ReportPeriod; label: string }[] = [
  { value: "day", label: "Last day" },
  { value: "week", label: "Last 7 days" },
  { value: "month", label: "Last 30 days" },
  { value: "quarter", label: "Last 90 days" }
];

function isPeriod(value: string | null): value is ReportPeriod {
  return PERIODS.some((p) => p.value === value);
}

/**
 * The manager report: a printable summary of a period for people who don't
 * read every run. "Save as PDF" prints the page; the toolbar and the app's
 * navigation are left out of the printout.
 */
export function ReportPage() {
  const [params, setParams] = useSearchParams();
  const period: ReportPeriod = isPeriod(params.get("period")) ? (params.get("period") as ReportPeriod) : "week";
  const cycle = params.get("cycle") ?? "";
  const query = useQuery({
    queryKey: ["report", period, cycle],
    queryFn: () => apiClient.getReport(period, cycle || undefined)
  });

  const update = (next: { period?: ReportPeriod; cycle?: string }) => {
    const merged = { period, cycle, ...next };
    setParams({ ...(merged.period !== "week" ? { period: merged.period } : {}), ...(merged.cycle ? { cycle: merged.cycle } : {}) });
  };

  return (
    <div>
      <div className="print:hidden">
        <PageHeader
          title="Report"
          subtitle="A summary of a period for managers and stakeholders: save it as a PDF, download slides or a spreadsheet, or copy any section."
          actions={
            <>
              {IS_STATIC_BUILD ? null : <DownloadMenu period={period} cycle={cycle} />}
              <Button onClick={() => window.print()} disabled={!query.data}>
                Save as PDF
              </Button>
            </>
          }
        />
        <div className="mb-6 flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-ink-300">
            Period
            <select
              value={period}
              onChange={(e) => update({ period: e.target.value as ReportPeriod })}
              className="rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-ink-100"
            >
              {PERIODS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <CyclePicker cycle={cycle} onChange={(next) => update({ cycle: next })} />
        </div>
      </div>
      {query.isLoading ? (
        <Spinner />
      ) : query.error ? (
        <EmptyState title="Couldn't build the report" message={query.error instanceof Error ? query.error.message : String(query.error)} />
      ) : query.data ? (
        <ReportDocument report={query.data} />
      ) : null}
    </div>
  );
}
