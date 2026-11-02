import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ManagerReport } from "../../lib/api-client";
import { ReportPage } from "./ReportPage";

const report: ManagerReport = {
  title: "Weekly test report",
  period: "week",
  days: 7,
  start: 1_761_350_400,
  end: 1_761_955_200,
  generated_at: 1_761_955_200,
  scope: [],
  summary: ["12 runs across 2 cycles; 10 green (83%).", "The test pass rate rose from 97.0% to 98.5%."],
  kpis: {
    health_score: 84,
    health_grade: "B",
    pass_rate: 98.5,
    prev_pass_rate: 97,
    runs: 12,
    prev_runs: 9,
    green: 10,
    prev_green: 6,
    coverage_pct: null,
    prev_coverage_pct: null,
    cycles: 2,
    failing_tests: 1,
    newly_flaky: 1,
    fixed: 2
  },
  trend: [
    { day: "2025-10-26", runs: 2, green: 2, pass_rate: 99 },
    { day: "2025-10-27", runs: 0, green: 0, pass_rate: null },
    { day: "2025-10-28", runs: 3, green: 1, pass_rate: 96.5 }
  ],
  cycles: [
    { cycle: "api", runs: 8, green: 7, pass_rate: 99.1, prev_pass_rate: 98, failing: 0, newly_flaky: 1, fixed: 2, slower: 0, health_score: 90 },
    { cycle: "web", runs: 4, green: 3, pass_rate: 96, prev_pass_rate: null, failing: 1, newly_flaky: 0, fixed: 0, slower: 0, health_score: 71 }
  ],
  still_failing: [{ cycle: "web", name: "test_checkout", detail: "" }],
  newly_flaky: [{ cycle: "api", name: "test_login", detail: "" }],
  fixed: [],
  slower: [],
  findings: [{ cycle: "web", title: "web is red", detail: "1 test failing in the latest run." }]
};

afterEach(() => vi.unstubAllGlobals());

describe("ReportPage", () => {
  it("shows the period's numbers, charts and lists, and a print button", async () => {
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve({ ok: true, status: 200, json: async () => (String(url).includes("/report") ? report : { items: [{ name: "web" }] }) })
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/report?cycle=web"]}>
          <ReportPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Weekly test report" })).toBeInTheDocument();
    expect(String(fetchMock.mock.calls.find((c) => String(c[0]).includes("/report"))?.[0])).toContain("cycle=web");
    expect(screen.getByText("84")).toBeInTheDocument();
    expect(screen.getByText("+1.5 pts")).toBeInTheDocument();
    expect(screen.getByText("The test pass rate rose from 97.0% to 98.5%.")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Test pass rate per day, from 99% on 2025-10-26 to 96.5% on 2025-10-28/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Runs per day: 5 runs, 3 green, 2 red" })).toBeInTheDocument();
    expect(screen.getByText("test_checkout")).toBeInTheDocument();
    expect(screen.getByText("No test was fixed this period.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy or export Cycles" })).toBeInTheDocument();

    const print = vi.fn();
    vi.stubGlobal("print", print);
    await waitFor(() => expect(screen.getByRole("button", { name: "Save as PDF" })).toBeEnabled());
    screen.getByRole("button", { name: "Save as PDF" }).click();
    expect(print).toHaveBeenCalled();
  });
});

describe("ReportPage downloads", () => {
  it("downloads the report as a file from the server", async () => {
    const blob = new Blob(["pptx"]);
    const fetchMock = vi.fn((url: string) => {
      const u = String(url);
      if (u.includes("/report/export"))
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({ "Content-Disposition": 'attachment; filename="weekly-test-report-2025-11-01.pptx"' }),
          blob: async () => blob
        });
      return Promise.resolve({ ok: true, status: 200, json: async () => (u.includes("/report") ? report : { items: [] }) });
    });
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn(() => "blob:report");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/report?period=month"]}>
          <ReportPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    await screen.findByRole("heading", { level: 1, name: "Weekly test report" });
    screen.getByRole("button", { name: "Download" }).click();
    (await screen.findByRole("menuitem", { name: /PowerPoint/ })).click();
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledWith(blob));
    const exportUrl = String(fetchMock.mock.calls.find((c) => String(c[0]).includes("/report/export"))?.[0]);
    expect(exportUrl).toContain("format=pptx");
    expect(exportUrl).toContain("period=month");
  });
});
