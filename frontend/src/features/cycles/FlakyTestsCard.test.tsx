import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { FlakyTestsCard } from "./FlakyTestsCard";

function renderCard(payload: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><FlakyTestsCard cycle="fake-api" /></MemoryRouter>
    </QueryClientProvider>
  );
  return fetchMock;
}

const base = { cycle: "fake-api", window: 20, runs_analyzed: 4, tests_analyzed: 39 };

describe("FlakyTestsCard", () => {
  it("lists flaky tests with their flip count and history", async () => {
    const fetchMock = renderCard({
      ...base,
      items: [
        {
          key: "k",
          name: "test_flaky_route",
          group: "tests.api",
          runs: 4,
          passed: 2,
          failed: 2,
          flips: 3,
          flip_rate: 1,
          last_status: "failed",
          history: ["passed", "failed", "passed", "failed"],
          red_runs: 2,
          cost_minutes: 14.4,
          owners: ["@org/api"],
          issue_url: "https://github.com/acme/shop/issues/new?title=Flaky+test%3A+test_flaky_route"
        }
      ]
    });

    await waitFor(() => expect(screen.getByText("test_flaky_route")).toBeInTheDocument());
    expect(screen.getByText(/owned by @org\/api/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open an issue" })).toHaveAttribute(
      "href",
      expect.stringContaining("github.com/acme/shop/issues/new")
    );
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/v1/analytics/flaky?cycle=fake-api");
    expect(screen.getByText("Flaky tests (1)")).toBeInTheDocument();
    expect(screen.getByText("3 flips in 4 runs")).toBeInTheDocument();
    expect(screen.getByText("cost 14 min · 2 red runs")).toBeInTheDocument();
    expect(screen.getByLabelText("History, oldest first: passed, failed, passed, failed")).toBeInTheDocument();
  });

  it("says when nothing is flaky", async () => {
    renderCard({ ...base, items: [] });
    await waitFor(() => expect(screen.getByText("No flaky tests.")).toBeInTheDocument());
  });

  it("asks for more runs when there is too little history", async () => {
    renderCard({ ...base, runs_analyzed: 1, items: [] });
    await waitFor(() => expect(screen.getByText(/Run this cycle a few more times/)).toBeInTheDocument());
  });

  const flaky = (name: string, extra: Record<string, unknown> = {}) => ({
    key: `k-${name}`,
    name,
    group: "tests.api",
    runs: 4,
    passed: 2,
    failed: 2,
    flips: 3,
    flip_rate: 1,
    last_status: "failed",
    history: ["passed", "failed", "passed", "failed"],
    owners: [],
    issue_url: null,
    ...extra
  });

  it("quarantines a flaky test from the card", async () => {
    const fetchMock = renderCard({ ...base, items: [flaky("test_open")] });
    fireEvent.click(await screen.findByRole("button", { name: "Quarantine test_open" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain("/api/v1/quarantine");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toMatchObject({ cycle: "fake-api", test: "test_open" });
    // The card reloads to show the new state.
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3));
    expect(String(fetchMock.mock.calls[2][0])).toContain("/api/v1/analytics/flaky");
  });

  it("shows quarantined tests with a Release button only for a stored exact entry", async () => {
    const fetchMock = renderCard({
      ...base,
      items: [
        flaky("test_stored", { quarantined: true, quarantine_entry: "test_stored" }),
        flaky("test_by_glob", { quarantined: true, quarantine_entry: null })
      ]
    });
    await waitFor(() => expect(screen.getAllByText("Quarantined")).toHaveLength(2));
    expect(screen.queryByRole("button", { name: /^Quarantine / })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Release test_by_glob from quarantine" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Release test_stored from quarantine" }));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain("/api/v1/quarantine?cycle=fake-api&test=test_stored");
    expect(init.method).toBe("DELETE");
  });

  it("says why a quarantine write was refused", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...base, items: [flaky("test_open")] }) })
      .mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ error: { message: "Missing or invalid API token." } })
      });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><FlakyTestsCard cycle="fake-api" /></MemoryRouter>
      </QueryClientProvider>
    );
    fireEvent.click(await screen.findByRole("button", { name: "Quarantine test_open" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Missing or invalid API token.");
  });

  it("shows the first 3 of a long list until asked for all", async () => {
    const items = Array.from({ length: 25 }, (_, n) => ({
      key: `k${n}`,
      name: `test_${n}`,
      group: "g",
      runs: 4,
      passed: 2,
      failed: 2,
      flips: 3,
      flip_rate: 1,
      last_status: "failed",
      history: ["passed", "failed", "passed", "failed"],
      owners: [],
      issue_url: null
    }));
    renderCard({ ...base, items });

    await waitFor(() => expect(screen.getByText("test_2")).toBeInTheDocument());
    expect(screen.queryByText("test_3")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 25" }));
    expect(screen.getByText("test_24")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show fewer" }));
    expect(screen.queryByText("test_24")).not.toBeInTheDocument();
  });
});
