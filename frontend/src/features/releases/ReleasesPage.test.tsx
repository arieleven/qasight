import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiClient, type ReleaseDetailResponse } from "../../lib/api-client";
import { ReleasePage, ReleasesPage } from "./ReleasesPage";

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/releases" element={<ReleasesPage />} />
          <Route path="/releases/:release" element={<ReleasePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const summary = {
  release: "2.4",
  first_run_at: 1_700_000_000,
  last_run_at: 1_700_000_100,
  runs: 3,
  cycles: 2,
  total: 3,
  passed: 2,
  failed: 1,
  skipped: 0,
  pass_rate: 66.7
};

afterEach(() => vi.restoreAllMocks());

describe("Releases", () => {
  it("explains how to record a release when there is none", async () => {
    vi.spyOn(apiClient, "getReleases").mockResolvedValue({ items: [] });
    renderAt("/releases");
    expect(await screen.findByText("No releases recorded yet")).toBeInTheDocument();
    expect(screen.getByText("qasight run --release 2.3")).toBeInTheDocument();
  });

  it("lists releases with their pass rate", async () => {
    vi.spyOn(apiClient, "getReleases").mockResolvedValue({ items: [summary] });
    renderAt("/releases");
    expect(await screen.findByRole("link", { name: "2.4" })).toHaveAttribute("href", "/releases/2.4");
    expect(screen.getByText("67% passed")).toBeInTheDocument();
  });

  it("shows new failures and the change from the release before", async () => {
    const detail: ReleaseDetailResponse = {
      summary,
      cycles: [{ cycle: "api", run_id: "a4", created_at: 1_700_000_100, runs: 2, total: 2, passed: 1, failed: 1, skipped: 0, flaky: 0 }],
      failing: [{ cycle: "api", key: "cart", name: "cart adds", status: "failed", message: "nope", run_id: "a4", new: true }],
      fixed: [{ cycle: "api", key: "pay", name: "pay works", status: "passed", message: "", run_id: "a4", new: false }],
      flaky: 0,
      previous: { ...summary, release: "2.3", pass_rate: 50, failed: 1 }
    };
    vi.spyOn(apiClient, "getRelease").mockResolvedValue(detail);
    renderAt("/releases/2.4");
    expect(await screen.findByText("cart adds")).toBeInTheDocument();
    expect(screen.getByText("New in this release")).toBeInTheDocument();
    expect(screen.getByText("pay works")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "2.3" })).toHaveAttribute("href", "/releases/2.3");
    expect(screen.getByText(/\+16\.7 points/)).toBeInTheDocument();
  });
});
