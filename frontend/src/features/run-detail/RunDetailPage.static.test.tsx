import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AI_SUMMARY_SNAPSHOT_MESSAGE } from "../../lib/static-backend";
import { RunDetailPage } from "./RunDetailPage";

vi.mock("../../lib/static-backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/static-backend")>()),
  IS_STATIC_BUILD: true
}));

function stubRun(aiSummary: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/runs/run-1/reports")) {
        return Promise.resolve({ ok: true, json: async () => ({ static_links: {}, artifact_links: [] }) });
      }
      if (url.endsWith("/api/v1/runs/run-1")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            run: {
              run_id: "run-1",
              test_kind: "pytest",
              returncode: 1,
              created_at: 1,
              started_at: 1,
              finished_at: 2,
              wall_duration_ms: 100,
              health_pct: 80
            }
          })
        });
      }
      if (url.endsWith("/api/v1/runs/run-1/ai-summary")) {
        return Promise.resolve({
          ok: true,
          json: async () => aiSummary
        });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    })
  );
}

function renderRun() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/runs/run-1"]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("RunDetailPage in the read-only demo", () => {
  it("explains a missing AI summary instead of showing an error code", async () => {
    stubRun({
      schema_version: "v1",
      run_id: "run-1",
      status: "no_summary_generated",
      limitations: ["summary_not_generated"],
      generated_at: 1,
      error_code: "summary_not_available"
    });
    renderRun();
    await waitFor(() => expect(screen.getByText(AI_SUMMARY_SNAPSHOT_MESSAGE)).toBeInTheDocument());
    expect(screen.queryByText(/summary_not_available/)).not.toBeInTheDocument();
  });

  it("marks the demo's made-up AI summary as a fake example", async () => {
    stubRun({
      schema_version: "v1",
      run_id: "run-1",
      status: "available",
      summary_text: "What failed: 2 tests failed.\nLikely cause: one shared error.\nNext steps: fix it.",
      limitations: ["demo_example"],
      provider: "demo-example",
      model: "made-up example, not a real AI answer",
      generated_at: 1
    });
    renderRun();
    expect(await screen.findByText(/one shared error/)).toBeInTheDocument();
    expect(screen.getByTestId("demo-example-note")).toHaveTextContent("Fake example for the live demo");
    expect(screen.queryByText(/Generated when this snapshot was built/)).not.toBeInTheDocument();
  });
});
