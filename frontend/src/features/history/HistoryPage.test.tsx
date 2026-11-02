import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { HistoryPage } from "./HistoryPage";

function run(run_id: string, cycle: string, created_at: number) {
  return { run_id, cycle, created_at, status: "COMPLETED", returncode: 0, health_pct: 100 };
}

function renderWithRuns(items: ReturnType<typeof run>[], path = "/runs") {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/v1/cycles")) {
      const names = [...new Set(items.map((item) => item.cycle))];
      return Promise.resolve({ ok: true, json: async () => ({ items: names.map((name) => ({ name, stage_count: 1 })) }) });
    }
    const cycle = new URL(url, "http://x").searchParams.get("cycle");
    // Like the API: ?cycle= keeps that cycle's runs.
    return Promise.resolve({ ok: true, json: async () => ({ items: items.filter((i) => !cycle || i.cycle === cycle) }) });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <HistoryPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("HistoryPage", () => {
  it("compares the latest run with the previous run of the same cycle", async () => {
    renderWithRuns([run("a-2", "a", 3), run("b-1", "b", 2), run("a-1", "a", 1)]);

    await waitFor(() => expect(screen.getByRole("link", { name: "Compare latest two" })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Compare latest two" })).toHaveAttribute(
      "href",
      "/compare?current_run_id=a-2&baseline_run_id=a-1"
    );
  });

  it("hides the compare button when the latest cycle has no earlier run", async () => {
    renderWithRuns([run("a-1", "a", 2), run("b-1", "b", 1)]);

    await waitFor(() => expect(screen.getByText("Runs")).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "Compare latest two" })).not.toBeInTheDocument();
  });

  it("filters to one cycle and says how often it passed", async () => {
    const failed = { ...run("a-2", "a", 3), returncode: 1, status: "FAILED" };
    renderWithRuns([failed, run("b-1", "b", 2), run("a-1", "a", 1)], "/runs?cycle=a");

    await waitFor(() => expect(screen.getByTestId("pass-rate")).toHaveTextContent("Passed 1 of the last 2 runs (50%)."));
    expect(screen.getByText("Runs of a, newest first.")).toBeInTheDocument();
    expect(screen.queryByText(/^b ·/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Cycle"), { target: { value: "" } });
    await waitFor(() => expect(screen.getByText("Every recorded run, newest first.")).toBeInTheDocument());
    expect(screen.queryByTestId("pass-rate")).not.toBeInTheDocument();
  });
});

describe("HistoryPage paging", () => {
  it("reaches runs older than the first page", async () => {
    const many = Array.from({ length: 45 }, (_, n) => run(`r-${n}`, "a", 100 - n));
    renderWithRuns(many);

    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(30));
    fireEvent.click(screen.getByRole("button", { name: "Show older runs" }));
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(45));
    expect(screen.queryByRole("button", { name: "Show older runs" })).not.toBeInTheDocument();
  });
});

describe("HistoryPage deleting runs", () => {
  it("deletes the chosen runs after a confirm, and never offers a running one", async () => {
    const items = [run("a-2", "a", 3), { ...run("a-1", "a", 2), status: "RUNNING" }, run("b-1", "b", 1)];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/runs:delete")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ deleted: ["a-2", "b-1"], test_rows_deleted: 2, snapshots_deleted: 0, skipped_running: [], not_found: [], errors: [], echo: init?.body })
        });
      }
      if (url.includes("/api/v1/run-settings")) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            config_found: true,
            retention: { days: 30, runs_per_cycle: null, enabled: true },
            cleanup_interval_hours: 6,
            default_stage_timeout_s: 600
          })
        });
      }
      if (url.includes("/api/v1/cycles")) return Promise.resolve({ ok: true, json: async () => ({ items: [] }) });
      return Promise.resolve({ ok: true, json: async () => ({ items }) });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/runs"]}>
          <HistoryPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByTestId("run-settings")).toHaveTextContent("Runs older than 30 days are deleted automatically every 6 hours."));
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Select runs" }));
    expect(screen.getByLabelText("Select run a-1")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Select run a-2"));
    fireEvent.click(screen.getByLabelText("Select run b-1"));
    expect(screen.getByTestId("delete-toolbar")).toHaveTextContent("2 runs selected.");

    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("runs:delete"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Yes, delete" }));

    await waitFor(() => expect(screen.getByTestId("delete-notice")).toHaveTextContent("Deleted 2 runs."));
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("runs:delete"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ run_ids: ["a-2", "b-1"] });
    expect((call?.[1] as RequestInit).method).toBe("POST");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});
