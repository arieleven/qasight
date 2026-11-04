import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { CyclesPage } from "./CyclesPage";

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <CyclesPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("CyclesPage", () => {
  it("lists cycles with stage counts, equipment badges and run buttons", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          items: [
            {
              name: "sample-pytests",
              description: "Pytest suite for the sample target repo.",
              stage_count: 1,
              equipment: ["pytest"],
              source: "config",
              latest_run: {
                run_id: "run-1234abcd",
                created_at: Date.now() / 1000 - 3 * 3600,
                status: "completed",
                returncode: 1,
                health_pct: 80,
                total_tests: 10,
                passed: 8,
                failed: 2,
                branch: "main",
                commit: "9c23517abcdef"
              }
            },
            {
              name: "sample-all-frameworks",
              description: "Run pytest, native Behave, then BehaveX.",
              stage_count: 3,
              equipment: ["behave", "behavex", "pytest"],
              source: "config"
            },
            {
              name: "web",
              description: "Recorded runs only.",
              stage_count: 1,
              equipment: ["junit"],
              source: "history"
            }
          ],
          config_path: "/repo/qasight.yaml"
        })
      })
    );

    renderPage();

    await waitFor(() => expect(screen.getByRole("link", { name: "sample-pytests" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "sample-pytests" })).toHaveAttribute("href", "/cycles/sample-pytests");
    expect(screen.getByText("3 stages")).toBeInTheDocument();
    expect(screen.getByText("behavex")).toBeInTheDocument();
    // The recorded-only cycle opens like the others but has nothing to run.
    expect(screen.getByRole("link", { name: "web" })).toHaveAttribute("href", "/cycles/web");
    expect(screen.getByText("Recorded")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Run" })).toHaveLength(2);
    expect(screen.getByText(/Defined in \/repo\/qasight.yaml/)).toBeInTheDocument();
  });

  it("shows each cycle's latest result on its card", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          items: [
            {
              name: "sample-pytests",
              description: null,
              stage_count: 1,
              equipment: ["pytest"],
              source: "config",
              latest_run: {
                run_id: "run-1234abcd",
                created_at: Date.now() / 1000 - 3 * 3600,
                status: "completed",
                returncode: 1,
                health_pct: 80,
                total_tests: 10,
                passed: 8,
                failed: 2,
                branch: "main",
                commit: "9c23517abcdef"
              }
            },
            { name: "never-ran", description: null, stage_count: 1, equipment: ["pytest"], source: "config", latest_run: null }
          ],
          config_path: "/repo/qasight.yaml"
        })
      })
    );

    renderPage();

    const card = await screen.findByTestId("latest-run-sample-pytests");
    expect(card).toHaveTextContent("80% passed");
    expect(card).toHaveTextContent("2 failing");
    expect(card).toHaveTextContent("main @ 9c23517");
    expect(screen.getByRole("link", { name: "3 h ago" })).toHaveAttribute("href", "/runs/run-1234abcd");
    expect(screen.getByText("No runs recorded yet.")).toBeInTheDocument();
  });

  it("says what to check when cycles can't be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503, json: async () => ({}) })
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("Couldn't load cycles")).toBeInTheDocument());
    expect(screen.getByText(/qasight config validate/)).toBeInTheDocument();
  });

  it("points at qasight ingest when there are no cycles at all", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], config_path: null }) })
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("No cycles yet")).toBeInTheDocument());
    expect(screen.getByText(/qasight ingest/)).toBeInTheDocument();
  });
});
