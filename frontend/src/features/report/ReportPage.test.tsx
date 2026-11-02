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
    oldest_open: [{ cycle: "web", name: "test_cart_total", since: 1_760_000_000, age_s: 12 * 86400, runs: 9, key: "k9" }],
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
  },
  signal: {
    red_runs: 10,
    prev_red_runs: 8,
    by_cause: [
      { cause: "product_bug", label: "Real bug", runs: 3 },
      { cause: "unmarked", label: "Not marked yet", runs: 2 },
      { cause: "flaky", label: "Flaky tests", runs: 5 }
    ],
    real_pct: 30,
    noise_pct: 50,
    unmarked_pct: 20,
    prev_real_pct: null,
    prev_noise_pct: 75,
    flaky_minutes: 42,
    prev_flaky_minutes: 30,
    flaky_red_runs: 5,
    flaky_branches: 2,
    costly: [{ cycle: "api", key: "k1", name: "test_payment_retry", cost_minutes: 21.5, retries: 3, red_runs: 4, branches: ["f1"] }]
  },
  teams: {
    owners_known: true,
    rows: [
      { owner: "@acme/payments", tests: 40, failing: 2, flaky: 1, flaky_minutes: 6.5, fixes: 2, median_fix_s: 7200, open_failures: 2, oldest_open_s: 3 * 86400 },
      { owner: "(no owner)", tests: 12, failing: 0, flaky: 0, flaky_minutes: 0, fixes: 0, median_fix_s: null, open_failures: 0, oldest_open_s: null }
    ]
  },
  hygiene: {
    tests: 1240,
    prev_tests: 1205,
    added: 40,
    removed: 5,
    cycles: [{ cycle: "api", tests: 1240, prev_tests: 1205, added: 40, removed: 5 }],
    added_tests: [{ cycle: "api", name: "test_refund_partial", detail: "" }],
    removed_tests: [],
    always_skipped: [{ cycle: "api", name: "test_legacy_export", detail: "skipped in 6 runs" }],
    always_skipped_count: 1,
    slow_never_failed: [{ cycle: "api", name: "test_full_sync", detail: "48.0s, passed all 20 runs" }],
    slow_never_failed_ms: 48_000
  },
  patterns: {
    failures: 20,
    top_tests: [{ cycle: "web", name: "test_checkout_flow", count: 12, detail: "" }],
    top_share_pct: 60,
    repeated_errors: [{ message: "TimeoutError: page.click: Timeout 30000ms exceeded", count: 9, tests: 3 }],
    retried: 6,
    executed: 400,
    retry_pct: 1.5,
    prev_retry_pct: 0.8,
    unstable_timing: [{ cycle: "web", name: "test_search", count: 4000, detail: "median 1.0s, slowest runs 5.0s" }],
    by_hour: [],
    by_weekday: [{ label: "Mon", runs: 6, red: 4 }],
    by_environment: [
      { label: "browser=firefox", runs: 6, red: 5 },
      { label: "browser=chrome", runs: 6, red: 1 }
    ],
    when_findings: ["Runs on browser=firefox failed 5.0x as often as the rest (5 of 6 red)."],
    caught_bugs: [{ cycle: "api", name: "test_refund_amount", count: 2, detail: "0 false alarms" }],
    only_noise: [{ cycle: "web", name: "test_banner_shows", count: 4, detail: "" }],
    days: [
      { day: "2025-10-26", retried: 2, executed: 150, retry_pct: 1.3, coverage_pct: 81.2 },
      { day: "2025-10-27", retried: 0, executed: 0, retry_pct: null, coverage_pct: null },
      { day: "2025-10-28", retried: 4, executed: 250, retry_pct: 1.6, coverage_pct: 82.4 }
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
    // Why runs were red, cost of flakiness
    expect(screen.getByRole("img", { name: "Red runs by cause: Real bug 3, Not marked yet 2, Flaky tests 5" })).toBeInTheDocument();
    expect(screen.getByText(/50% noise, was 75%/)).toBeInTheDocument();
    expect(screen.getByText("test_payment_retry")).toBeInTheDocument();
    expect(screen.getByText(/5 red runs, 2 branches held up/)).toBeInTheDocument();
    // By team, suite size and hygiene
    expect(screen.getByText("@acme/payments")).toBeInTheDocument();
    expect(screen.getByText("2, 3.0d")).toBeInTheDocument();
    expect(screen.getByText(/\+35 since the period before \(40 added, 5 removed\)/)).toBeInTheDocument();
    expect(screen.getByText("test_legacy_export")).toBeInTheDocument();
    expect(screen.getByText("test_full_sync")).toBeInTheDocument();
    // Failure patterns
    expect(screen.getByText("1 test caused 60% of the 20 test failures.")).toBeInTheDocument();
    expect(screen.getByText("TimeoutError: page.click: Timeout 30000ms exceeded")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Line coverage per day, from 81.2% on 2025-10-26 to 82.4% on 2025-10-28/ })).toBeInTheDocument();
    expect(screen.getByText("Runs on browser=firefox failed 5.0x as often as the rest (5 of 6 red).")).toBeInTheDocument();
    expect(screen.getByText("test_refund_amount")).toBeInTheDocument();
    expect(screen.getByText("test_banner_shows")).toBeInTheDocument();
    expect(screen.getByText("test_search")).toBeInTheDocument();
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
