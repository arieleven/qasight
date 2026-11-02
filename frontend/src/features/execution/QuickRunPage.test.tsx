import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { QuickRunPage } from "./QuickRunPage";

class FakeEventSource {
  private listeners: Record<string, Array<(evt: MessageEvent) => void>> = {};
  onerror: ((evt: Event) => void) | null = null;

  addEventListener(type: string, cb: (evt: MessageEvent) => void) {
    this.listeners[type] = this.listeners[type] || [];
    this.listeners[type].push(cb);
  }

  emit(type: string, data: unknown) {
    const evt = { data: JSON.stringify(data) } as MessageEvent;
    for (const cb of this.listeners[type] || []) {
      cb(evt);
    }
  }

  close() {}
}

describe("QuickRunPage", () => {
  it("posts an ad-hoc execution and follows the cycle event stream", async () => {
    const source = new FakeEventSource();
    // A constructor mock: vitest 4+ refuses `new` on an arrow implementation.
    vi.stubGlobal("EventSource", vi.fn(function () { return source; }));
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        execution_id: "adhoc-1",
        status: "queued",
        events_url: "http://localhost:8000/api/v1/cycle-executions/adhoc-1/events",
        summary_url: "http://localhost:8000/api/v1/cycle-executions/adhoc-1"
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <QuickRunPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    fireEvent.change(screen.getByDisplayValue("pytest"), { target: { value: "behave" } });
    fireEvent.change(screen.getByDisplayValue("-q"), { target: { value: "features/smoke.feature" } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(screen.getByText("execution adhoc-1")).toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v1/adhoc-executions");
    expect(JSON.parse(String(init.body))).toMatchObject({
      framework: "behave",
      target_repo: ".",
      args: ["features/smoke.feature"],
      persist: true
    });
    expect(EventSource).toHaveBeenCalledWith("http://localhost:8000/api/v1/cycle-executions/adhoc-1/events");

    source.emit("stage_started", { event: "stage_started", stage: "behave", framework: "behave", index: 0, count: 1 });
    source.emit("stage_finished", {
      event: "stage_finished",
      stage: "behave",
      framework: "behave",
      returncode: 0,
      duration_s: 1.5,
      log_path: null,
      timed_out: false
    });
    source.emit("plan_finished", {
      event: "plan_finished",
      plan: "adhoc",
      aggregate_returncode: 0,
      exit_code: 0,
      duration_s: 1.5
    });

    await waitFor(() => expect(screen.getByTestId("run-outcome")).toHaveTextContent("Run passed."));
    // The finished run shows up in the lists on screen without a reload.
    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    expect(screen.getByText("1.5s")).toBeInTheDocument();
  });
});

describe("QuickRunPage Stop button", () => {
  it("stops a running run and says it stopped", async () => {
    const source = new FakeEventSource();
    vi.stubGlobal("EventSource", vi.fn(function () { return source; }));
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/cancel")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ execution_id: "adhoc-2", cycle: "adhoc", status: "running" })
        });
      }
      return Promise.resolve({
        ok: true,
        status: 202,
        json: async () => ({
          execution_id: "adhoc-2",
          status: "running",
          events_url: "http://localhost:8000/api/v1/cycle-executions/adhoc-2/events",
          summary_url: "http://localhost:8000/api/v1/cycle-executions/adhoc-2"
        })
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <QuickRunPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(screen.getByTestId("run-stop")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Force stop" })).toBeInTheDocument());
    const cancel = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/cancel"));
    expect(String(cancel?.[0])).toContain("/api/v1/cycle-executions/adhoc-2/cancel");
    expect((cancel?.[1] as RequestInit).method).toBe("POST");

    source.emit("plan_aborted", { event: "plan_aborted", plan: "adhoc", reason: "stopped", completed_stages: 1 });
    await waitFor(() => expect(screen.getByText("Run stopped.")).toBeInTheDocument());
    expect(screen.queryByTestId("run-stop")).not.toBeInTheDocument();
  });
});
