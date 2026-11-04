import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { CycleDetailPage } from "./CycleDetailPage";

const responses: Record<string, unknown> = {
  "/api/v1/cycles/web": {
    name: "web",
    description: "Recorded runs only: this cycle isn't in qasight.yaml, so it can't be run from here.",
    stages: [{ name: "junit", equipment: "junit", target_repo: "", args: [], timeout_s: null, workers: null }],
    trigger: null,
    source: "history"
  },
  "/api/v1/analytics/flaky": { cycle: "web", window: 20, runs_analyzed: 3, tests_analyzed: 5, items: [] },
  "/api/v1/analytics/tests": { cycle: "web", runs_considered: 3, items: [] }
};

describe("CycleDetailPage", () => {
  it("shows a recorded-only cycle without a run panel or config fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const path = new URL(url, "http://x").pathname;
        return Promise.resolve({ ok: true, json: async () => responses[path] ?? {} });
      })
    );

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/cycles/web"]}>
          <Routes>
            <Route path="/cycles/:name" element={<CycleDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText(/can't be run from here/)).toBeInTheDocument();
    // One recorded stage with nothing to configure: no Stages card repeating the header.
    expect(screen.queryByRole("heading", { name: "Stages (1)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Run/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Timeout")).not.toBeInTheDocument();
  });
});
