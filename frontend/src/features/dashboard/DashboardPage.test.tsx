import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "../../app/AppShell";
import { DashboardPage, trendSemantic } from "./DashboardPage";

function overviewPayload(overrides: Record<string, unknown> = {}) {
  return {
    headline_kpis: {
      latest_run_id: "run-1",
      latest_status: "COMPLETED",
      health_pct: 98,
      pass_count: 12,
      fail_count: 1,
      duration_ms: 1222,
      cycle: "smoke",
      baseline_run_id: "run-0"
    },
    trend_indicators: {
      health: { direction: "up", delta_abs: 2, delta_pct: 2.1 },
      failed_count: { direction: "down", delta_abs: -1, delta_pct: -50 },
      duration: { direction: "down", delta_abs: -100, delta_pct: -7.5 }
    },
    reliability_rollup: {
      status_summary: { regressions: 1, improvements: 3, unchanged: 2, unknown: 0 },
      top_highlights: ["Failed tests improved by 1 tests."]
    },
    performance_rollup: {
      status_summary: { regressions: 0, improvements: 2, unchanged: 1, unknown: 0 },
      top_highlights: ["Wall duration improved by 100.00 ms."]
    },
    report_links: {
      allure: { url: "http://allure/run-1", state: "available" },
      behave: { url: "/history/run-1/allure_reports/behavex/index.html", state: "available" }
    },
    recent_runs: [
      {
        run_id: "run-1",
        cycle: "fake-api",
        created_at: 1,
        status: "COMPLETED",
        returncode: 0,
        health_pct: 98,
        duration_ms: 1222,
        run_detail_url: "/runs/run-1",
        compare_url: "/compare?current_run_id=run-1&baseline_run_id=run-0"
      }
    ],
    data_freshness: { generated_at: 1, source_window_size: 2, degraded: false, notes: [] },
    ...overrides
  };
}

function stubOverviewFetch(payload: Record<string, unknown>, attention?: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/dashboard/overview")) {
        return Promise.resolve({ ok: true, json: async () => payload });
      }
      if (url.includes("/api/v1/project-health")) {
        return Promise.resolve({ ok: true, json: async () => ({ score: 84, grade: "B", cycles: 1, runs_considered: 3, tests: 10, signals: [], by_cycle: [], findings: [] }) });
      }
      if (attention && url.includes("/api/v1/dashboard/attention")) {
        return Promise.resolve({ ok: true, json: async () => attention });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    })
  );
}

function renderDashboard() {
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <AppShell />,
        children: [{ index: true, element: <DashboardPage /> }]
      }
    ],
    { initialEntries: ["/"] }
  );
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

describe("DashboardPage", () => {
  it("renders overview cards and quick links", async () => {
    stubOverviewFetch(overviewPayload());
    renderDashboard();

    await waitFor(() => expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument());
    // One health score in the app: the Project health page's, linked from here.
    const health = await screen.findByRole("link", { name: "Project health: 84 out of 100, grade B. Open project health" });
    expect(health).toHaveAttribute("href", "/health");
    expect(screen.getByTestId("failed-trend")).toHaveTextContent("Better than last run");
    expect(screen.getByRole("link", { name: "Allure report" })).toHaveAttribute("href", "http://allure/run-1");
    // Without attention data the card steps aside rather than showing an error.
    expect(screen.queryByTestId("needs-attention")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^fake-api · / })).toHaveAttribute("href", "/runs/run-1");
  });

  it("names the cycle and the same-cycle run the trends compare against", async () => {
    stubOverviewFetch(overviewPayload());
    renderDashboard();

    await waitFor(() => expect(screen.getByTestId("trend-baseline")).toHaveTextContent("Latest run: smoke."));
    expect(screen.getByRole("link", { name: "previous smoke run" })).toHaveAttribute(
      "href",
      "/compare?current_run_id=run-1&baseline_run_id=run-0"
    );
  });

  it("says there is no trend for the first run of a cycle", async () => {
    const payload = overviewPayload();
    stubOverviewFetch({ ...payload, headline_kpis: { ...payload.headline_kpis, baseline_run_id: null } });
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByTestId("trend-baseline")).toHaveTextContent("No earlier run of this cycle")
    );
    expect(screen.queryByRole("link", { name: "previous smoke run" })).not.toBeInTheDocument();
  });

  it("renders the first-run guide when no runs exist", async () => {
    stubOverviewFetch(
      overviewPayload({
        headline_kpis: {
          latest_run_id: null,
          latest_status: null,
          health_pct: null,
          pass_count: null,
          fail_count: null,
          duration_ms: null
        },
        recent_runs: [],
        data_freshness: { generated_at: 1, source_window_size: 0, degraded: true, notes: ["no_runs_available"] }
      })
    );
    renderDashboard();

    await waitFor(() => expect(screen.getByTestId("first-run-guide")).toBeInTheDocument());
    expect(screen.getByText("Welcome to QASight")).toBeInTheDocument();
    expect(screen.getByText(/qasight ingest/)).toBeInTheDocument();
    expect(within(screen.getByTestId("first-run-guide")).getByText("qasight init")).toBeInTheDocument();
    expect(within(screen.getByTestId("first-run-guide")).getByRole("link", { name: "Try a file" })).toHaveAttribute(
      "href",
      "/try"
    );
  });

  it("leads with what needs attention: red cycles, new flaky tests, slower tests", async () => {
    stubOverviewFetch(overviewPayload(), {
      cycles: 4,
      passing_cycles: 2,
      failing: [
        { cycle: "web", run_id: "run-9", created_at: 1, branch: "main", failed_tests: 4, new_failures: 1, failing_runs: 3 },
        // Red with no failing test: a stage crashed (e.g. a conftest import error).
        { cycle: "docs", run_id: "run-8", created_at: 1, branch: null, failed_tests: 0, new_failures: 0, failing_runs: 1 }
      ],
      newly_flaky: [{ cycle: "web", key: "k1", name: "test_checkout", slowdown_ms: null, slowdown_pct: null }],
      slower: [{ cycle: "api", key: "k2", name: "test_search", slowdown_ms: 1500, slowdown_pct: 50 }]
    });
    renderDashboard();

    const card = await screen.findByTestId("needs-attention");
    expect(card).toHaveTextContent("2 of 4 cycles passed their latest run; 2 cycles are red.");
    expect(screen.getByRole("link", { name: "web" })).toHaveAttribute("href", "/runs/run-9");
    expect(card).toHaveTextContent("4 tests failing, 1 new");
    expect(card).toHaveTextContent("failed outside its tests");
    expect(card).not.toHaveTextContent("0 tests failing");
    expect(screen.getByText("Red for 3 runs")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "test_checkout" })).toHaveAttribute("href", "/cycles/web/test?key=k1");
    expect(card).toHaveTextContent("api · +1.5 s (+50%)");
    // The card comes before the latest run's numbers.
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings.indexOf("Needs attention")).toBeLessThan(headings.indexOf("Recent runs"));
  });

  it("opens and closes Needs attention on click, and remembers it", async () => {
    stubOverviewFetch(overviewPayload(), {
      cycles: 2,
      passing_cycles: 1,
      failing: [{ cycle: "web", run_id: "run-9", created_at: 1, branch: "main", failed_tests: 4, new_failures: 0, failing_runs: 1 }],
      newly_flaky: [],
      slower: []
    });
    const { unmount } = renderDashboard();
    const toggle = within(await screen.findByTestId("needs-attention")).getByRole("button", { name: /^Needs attention/ });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(screen.getByTestId("needs-attention")).queryByText("web")).not.toBeInTheDocument();
    expect(screen.getByTestId("needs-attention")).toHaveTextContent("1 cycle red");
    unmount();

    renderDashboard();
    expect(within(await screen.findByTestId("needs-attention")).getByRole("button", { name: /^Needs attention/ })).toHaveAttribute("aria-expanded", "false");
    localStorage.clear();
  });

  it("shows the last 3 recent runs until asked for all", async () => {
    const run = overviewPayload().recent_runs[0];
    const runs = Array.from({ length: 5 }, (_, n) => ({ ...run, run_id: `run-${n}`, run_detail_url: `/runs/run-${n}` }));
    stubOverviewFetch(overviewPayload({ recent_runs: runs }));
    renderDashboard();
    const list = await screen.findByTestId("recent-runs");
    expect(list.querySelectorAll("li")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Show all 5" }));
    expect(screen.getByTestId("recent-runs").querySelectorAll("li")).toHaveLength(5);
  });

  it("says when nothing needs you", async () => {
    stubOverviewFetch(overviewPayload(), { cycles: 2, passing_cycles: 2, failing: [], newly_flaky: [], slower: [] });
    renderDashboard();
    expect(await screen.findByTestId("needs-attention")).toHaveTextContent(
      "All 2 cycles passed their latest run. Nothing needs you."
    );
  });

  it("says plainly when every cycle is red", async () => {
    const red = { run_id: "r", created_at: 1, branch: null, failed_tests: 1, new_failures: 0, failing_runs: 1 };
    stubOverviewFetch(overviewPayload(), {
      cycles: 2,
      passing_cycles: 0,
      failing: [
        { ...red, cycle: "web" },
        { ...red, cycle: "api" }
      ],
      newly_flaky: [],
      slower: [{ cycle: "api", key: "k2", name: "test_search", slowdown_ms: 1100, slowdown_pct: 95.5 }]
    });
    renderDashboard();
    const card = await screen.findByTestId("needs-attention");
    expect(card).toHaveTextContent("All 2 cycles are red.");
    expect(card).toHaveTextContent("+1.1 s (+96%)");
  });

  it("renders degraded notice and unavailable report links when data exists", async () => {
    stubOverviewFetch(
      overviewPayload({
        report_links: {
          allure: { url: null, state: "unknown" },
          behave: { url: null, state: "missing" }
        },
        data_freshness: { generated_at: 1, source_window_size: 1, degraded: true, notes: ["single_run_window"] }
      })
    );
    renderDashboard();

    await waitFor(() => expect(screen.getByText(/Some metrics are degraded:/)).toBeInTheDocument());
    // Reports that aren't there get no line at all.
    expect(screen.queryByText(/Allure report/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Behave report/)).not.toBeInTheDocument();
  });

  it("maps trend semantics correctly", () => {
    expect(trendSemantic({ direction: "up", delta_abs: 1, delta_pct: 1 }, false).label).toBe("improved");
    expect(trendSemantic({ direction: "up", delta_abs: 1, delta_pct: 1 }, true).label).toBe("regressed");
    expect(trendSemantic({ direction: "flat", delta_abs: 0, delta_pct: 0 }, true).label).toBe("flat");
    expect(trendSemantic({ direction: "unknown", delta_abs: null, delta_pct: null }, false).label).toBe("unknown");
  });
});
