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
    { day: "2025-10-26", runs: 2, green: 2, pass_rate: 99, median_duration_ms: 240_000, ci_minutes: 8 },
    { day: "2025-10-27", runs: 0, green: 0, pass_rate: null, median_duration_ms: null, ci_minutes: 0 },
    { day: "2025-10-28", runs: 3, green: 1, pass_rate: 96.5, median_duration_ms: 330_000, ci_minutes: 16.5 }
  ],
  cycles: [
    { cycle: "api", runs: 8, green: 7, pass_rate: 99.1, prev_pass_rate: 98, failing: 0, newly_flaky: 1, fixed: 2, slower: 0, health_score: 90 },
    { cycle: "web", runs: 4, green: 3, pass_rate: 96, prev_pass_rate: null, failing: 1, newly_flaky: 0, fixed: 0, slower: 0, health_score: 71 }
  ],
  still_failing: [{ cycle: "web", name: "test_checkout", detail: "" }],
  newly_flaky: [{ cycle: "api", name: "test_login", detail: "" }],
  fixed: [],
  slower: [],
  findings: [{ cycle: "web", title: "web is red", detail: "1 test failing in the latest run." }],
  delivery: {
    default_branch: "main",
    fixes: 3,
    median_fix_s: 2.5 * 86400,
    prev_median_fix_s: 4 * 86400,
    open_failures: 2,
    open_by_age: [
      { label: "Under a day", count: 1 },
      { label: "1–7 days", count: 0 },
      { label: "1–4 weeks", count: 1 },
      { label: "Over a month", count: 0 }
    ],
    oldest_open: [{ cycle: "web", name: "test_cart_total", since: 1_760_000_000, age_s: 12 * 86400, runs: 9 }],
    ci_minutes: 24.5,
    prev_ci_minutes: 20,
    red_pct: 18,
    prev_red_pct: 25,
    longest_red_s: 2 * 86400,
    first_try_green_pct: 75,
    prev_first_try_green_pct: null,
    first_try_commits: 8,
    cycles: [
      {
        cycle: "web",
        fixes: 1,
        median_fix_s: 3600,
        prev_median_fix_s: null,
        open_failures: 2,
        suite_median_ms: 330_000,
        suite_p90_ms: 420_000,
        prev_suite_median_ms: 240_000,
        ci_minutes: 16.5,
        prev_ci_minutes: 12,
        grown_stage: "e2e",
        grown_stage_before_ms: 120_000,
        grown_stage_after_ms: 200_000,
        red_pct: 18,
        prev_red_pct: 25,
        longest_red_s: 2 * 86400,
        first_try_green_pct: 75,
        first_try_commits: 8
      }
    ]
  }
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
    // Speed and stability
    expect(screen.getByText("2.5d")).toBeInTheDocument();
    expect(screen.getByText("· was 4.0d")).toBeInTheDocument();
    expect(screen.getByText("main red")).toBeInTheDocument();
    expect(screen.getByText("25 min")).toBeInTheDocument();
    expect(screen.getByText("75% of 8 pull request commits passed on their first run.")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Median suite time per day, from 4.0m on 2025-10-26 to 5.5m on 2025-10-28/ })).toBeInTheDocument();
    expect(screen.getByText("test_cart_total")).toBeInTheDocument();
    expect(screen.getByText("e2e: 2m 0s → 3m 20s")).toBeInTheDocument();
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
