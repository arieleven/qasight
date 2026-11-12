import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ComparePage } from "./ComparePage";

describe("ComparePage", () => {
  it("renders comparison data for selected runs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/runs?")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              items: [
                { run_id: "run-3", cycle: "self-test", created_at: 3, returncode: 0, status: "COMPLETED", health_pct: 100, links_under_static: {} },
                { run_id: "run-2", cycle: "fake-api", created_at: 2, returncode: 1, status: "FAILED", health_pct: 90, links_under_static: {} },
                { run_id: "run-1", cycle: "fake-api", created_at: 1, returncode: 0, status: "COMPLETED", health_pct: 99, links_under_static: {} }
              ]
            })
          });
        }
        if (url.includes("/api/v1/analytics/delta")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              comparison: {
                current_run_id: "run-2",
                baseline_run_id: "run-1",
                current_test_kind: "pytest",
                baseline_test_kind: "pytest"
              },
              metrics: {
                reliability: {
                  total_tests: _metric("improvement", 120, 100, 20, 20, "higher_is_better", "tests"),
                  passed: _metric("improvement", 100, 95, 5, 5.26, "higher_is_better", "tests"),
                  failed: _metric("regression", 8, 2, 6, 300, "lower_is_better", "tests"),
                  broken: _metric("neutral", 0, 0, 0, null, "lower_is_better", "tests"),
                  skipped: _metric("neutral", 1, 1, 0, 0, "lower_is_better", "tests"),
                  health_pct: _metric("regression", 92, 98, -6, -6.12, "higher_is_better", "pct")
                },
                performance: {
                  wall_duration_ms: _metric("regression", 1300, 1100, 200, 18.18, "lower_is_better", "ms"),
                  metrics_duration_ms: _metric("regression", 1200, 1000, 200, 20, "lower_is_better", "ms"),
                  // +1.5 ms is under the per-test floor: the API calls it neutral, within noise.
                  avg_case_ms: { ..._metric("neutral", 10.4, 8.9, 1.5, 16.85, "lower_is_better", "ms"), reason: "within_noise" }
                }
              },
              status_summary: {
                regressions: ["failed", "health_pct", "wall_duration_ms", "metrics_duration_ms"],
                improvements: ["total_tests", "passed"],
                unchanged: ["broken", "skipped", "avg_case_ms"],
                unknown: []
              },
              highlights: ["Failed tests worsened by 6 tests."]
            })
          });
        }
        return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
      })
    );

    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/compare"]}>
          <Routes>
            <Route path="/compare" element={<ComparePage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    // The heading shows while runs load too, so wait for the pickers themselves.
    await waitFor(() => expect(screen.getByLabelText("Current run")).toBeInTheDocument());

    // Pickers name runs by cycle and time, grouped by cycle, with the short id secondary.
    const currentPicker = screen.getByLabelText("Current run");
    expect(currentPicker.querySelectorAll("optgroup")).toHaveLength(2);
    expect(currentPicker.querySelector('option[value="run-2"]')?.textContent).toMatch(/^fake-api · .+ · run-2$/);

    // Choosing a cycle selects its latest run as current and the one before it as baseline.
    fireEvent.change(screen.getByLabelText("Cycle"), { target: { value: "fake-api" } });
    expect(screen.getByLabelText("Current run")).toHaveValue("run-2");
    expect(screen.getByLabelText("Baseline run")).toHaveValue("run-1");

    await waitFor(() => expect(screen.getByTestId("status-summary")).toHaveTextContent("4 metrics worse"));
    expect(screen.getByTestId("status-summary")).toHaveTextContent("2 better");

    const failedRow = screen.getByTestId("metric-failed");
    expect(failedRow).toHaveTextContent("Failed");
    expect(failedRow).toHaveTextContent("+6");
    expect(failedRow).toHaveTextContent("Regression");
    expect(screen.getByTestId("metric-health_pct")).toHaveTextContent("−6.0 pp");
    expect(screen.getByTestId("metric-wall_duration_ms")).toHaveTextContent("1.30 s");
    expect(screen.getByTestId("metric-avg_case_ms")).toHaveTextContent("Within noise");
    expect(screen.queryByText(/state=/)).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("Failed tests worsened by 6 tests.")).toBeInTheDocument();

    // Mixing cycles is allowed but flagged.
    fireEvent.change(screen.getByLabelText("Cycle"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Baseline run"), { target: { value: "run-3" } });
    expect(screen.getByRole("status")).toHaveTextContent("different cycles (self-test and fake-api)");
  });
});

function _metric(
  classification: "regression" | "improvement" | "neutral" | "unknown",
  current_value: number | null,
  baseline_value: number | null,
  absolute_delta: number | null,
  relative_delta_pct: number | null,
  direction: "higher_is_better" | "lower_is_better",
  unit: "tests" | "pct" | "ms"
) {
  return {
    current_value,
    baseline_value,
    absolute_delta,
    relative_delta_pct,
    classification,
    reason: null,
    direction,
    unit
  };
}

