import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "../app/AppShell";
import { ComparePage } from "../features/compare/ComparePage";
import { CycleDetailPage } from "../features/cycles/CycleDetailPage";
import { CyclesPage } from "../features/cycles/CyclesPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { HistoryPage } from "../features/history/HistoryPage";
import { RunDetailPage } from "../features/run-detail/RunDetailPage";

describe("happy path", () => {
  it("walks dashboard, cycles, runs, run detail and compare", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/v1/cycles")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                {
                  name: "sample-pytests",
                  description: "Pytest suite for the sample target repo.",
                  stage_count: 1,
                  equipment: ["pytest"],
                  source: "config"
                }
              ],
              config_path: "/repo/qasight.yaml"
            })
          });
        }
        if (url.endsWith("/api/v1/cycles/sample-pytests")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              name: "sample-pytests",
              description: "Pytest suite for the sample target repo.",
              stages: [
                {
                  name: "pytest-sample",
                  equipment: "pytest",
                  target_repo: "sample_target_repo",
                  args: ["-q", "tests"],
                  timeout_s: 600,
                  workers: 4
                }
              ],
              trigger: null,
              source: "config"
            })
          });
        }
        if (url.includes("/api/v1/dashboard/overview")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              headline_kpis: {
                latest_run_id: "run-1",
                latest_status: "COMPLETED",
                health_pct: 100,
                pass_count: 98,
                fail_count: 2,
                duration_ms: 1000
              },
              trend_indicators: {
                health: { direction: "up", delta_abs: 1, delta_pct: 1 },
                failed_count: { direction: "down", delta_abs: -1, delta_pct: -33.3 },
                duration: { direction: "down", delta_abs: -100, delta_pct: -9.1 }
              },
              reliability_rollup: {
                status_summary: { regressions: 0, improvements: 4, unchanged: 2, unknown: 0 },
                top_highlights: ["Failed tests improved by 3 tests."]
              },
              performance_rollup: {
                status_summary: { regressions: 0, improvements: 3, unchanged: 0, unknown: 0 },
                top_highlights: ["Wall duration improved by 200.00 ms."]
              },
              report_links: {
                allure: { url: "http://allure/report", state: "available" },
                behave: { url: "history/run-1/behave/index.html", state: "available" }
              },
              recent_runs: [
                {
                  run_id: "run-1",
                  created_at: 1,
                  returncode: 0,
                  status: "COMPLETED",
                  health_pct: 100,
                  duration_ms: 1000,
                  run_detail_url: "/runs/run-1",
                  compare_url: "/compare?current_run_id=run-1&baseline_run_id=run-2"
                },
                {
                  run_id: "run-2",
                  created_at: 0.5,
                  returncode: 1,
                  status: "FAILED",
                  health_pct: 92,
                  duration_ms: 1200,
                  run_detail_url: "/runs/run-2",
                  compare_url: null
                }
              ],
              data_freshness: {
                generated_at: 1,
                source_window_size: 2,
                degraded: false,
                notes: []
              }
            })
          });
        }
        if (url.includes("/api/v1/runs?")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                {
                  run_id: "run-1",
                  created_at: 1,
                  returncode: 0,
                  status: "COMPLETED",
                  health_pct: 100,
                  links_under_static: {}
                },
                {
                  run_id: "run-2",
                  created_at: 0.5,
                  returncode: 1,
                  status: "FAILED",
                  health_pct: 92,
                  links_under_static: {}
                }
              ]
            })
          });
        }
        if (url.endsWith("/api/v1/runs/run-1")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              run: {
                run_id: "run-1",
                test_kind: "pytest",
                returncode: 0,
                created_at: 1,
                started_at: 1,
                finished_at: 2,
                wall_duration_ms: 1000,
                health_pct: 100
              }
            })
          });
        }
        if (url.endsWith("/api/v1/runs/run-1/reports")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              static_links: { pytest: "history/run-1/index.html" },
              artifact_links: ["allure_report.html"]
            })
          });
        }
        if (url.includes("/api/v1/analytics/delta")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              comparison: {
                current_run_id: "run-1",
                baseline_run_id: "run-2",
                current_test_kind: "pytest",
                baseline_test_kind: "pytest"
              },
              metrics: {
                reliability: {
                  total_tests: _metric(100, 100, 0, 0, "neutral", "higher_is_better", "tests"),
                  passed: _metric(98, 95, 3, 3.1579, "improvement", "higher_is_better", "tests"),
                  failed: _metric(2, 5, -3, -60, "improvement", "lower_is_better", "tests"),
                  broken: _metric(0, 0, 0, null, "neutral", "lower_is_better", "tests"),
                  skipped: _metric(0, 0, 0, null, "neutral", "lower_is_better", "tests"),
                  health_pct: _metric(98, 95, 3, 3.1579, "improvement", "higher_is_better", "pct")
                },
                performance: {
                  wall_duration_ms: _metric(1000, 1200, -200, -16.6667, "improvement", "lower_is_better", "ms"),
                  metrics_duration_ms: _metric(900, 1100, -200, -18.1818, "improvement", "lower_is_better", "ms"),
                  avg_case_ms: _metric(10, 12, -2, -16.6667, "improvement", "lower_is_better", "ms")
                }
              },
              status_summary: {
                regressions: [],
                improvements: ["passed", "failed", "health_pct", "wall_duration_ms", "metrics_duration_ms", "avg_case_ms"],
                unchanged: ["total_tests", "broken", "skipped"],
                unknown: []
              },
              highlights: ["Failed tests improved by 3 tests."]
            })
          });
        }
        return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
      })
    );

    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: <AppShell />,
          children: [
            { index: true, element: <DashboardPage /> },
            { path: "/cycles", element: <CyclesPage /> },
            { path: "/cycles/:name", element: <CycleDetailPage /> },
            { path: "/runs", element: <HistoryPage /> },
            { path: "/compare", element: <ComparePage /> },
            { path: "/runs/:runId", element: <RunDetailPage /> }
          ]
        }
      ],
      {
        initialEntries: ["/"]
      }
    );

    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument());

    // Cycle discovery: the list shows what's defined, detail shows the stages.
    await router.navigate("/cycles");
    await waitFor(() => expect(screen.getByRole("link", { name: "sample-pytests" })).toBeInTheDocument());
    expect(screen.getByText("Pytest suite for the sample target repo.")).toBeInTheDocument();
    await router.navigate("/cycles/sample-pytests");
    await waitFor(() => expect(screen.getByText("pytest-sample")).toBeInTheDocument());
    expect(screen.getByTestId("run-panel-form")).toBeInTheDocument();

    await router.navigate("/runs");
    await waitFor(() => expect(screen.getByText("run-1")).toBeInTheDocument());
    await router.navigate("/runs/run-1");
    await waitFor(() => expect(screen.getByText("run-1")).toBeInTheDocument());
    await router.navigate("/compare?current_run_id=run-1&baseline_run_id=run-2");
    await waitFor(() => expect(screen.getByTestId("status-summary")).toHaveTextContent("6 better"));
    expect(screen.getByRole("heading", { name: "Reliability" })).toBeInTheDocument();
    // Five pages in one test: ~1.5 s alone, but it went past vitest's 5 s
    // default on a busy machine, so give it room instead of flaking.
  }, 15_000);
});

function _metric(
  current: number | null,
  baseline: number | null,
  absolute: number | null,
  relative: number | null,
  classification: "regression" | "improvement" | "neutral" | "unknown",
  direction: "higher_is_better" | "lower_is_better",
  unit: "tests" | "pct" | "ms"
) {
  return {
    current_value: current,
    baseline_value: baseline,
    absolute_delta: absolute,
    relative_delta_pct: relative,
    classification,
    reason: null,
    direction,
    unit
  };
}
