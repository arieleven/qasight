import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { RunPanel } from "./RunPanel";

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

function renderPanel(props: Parameters<typeof RunPanel>[0] = {}) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <RunPanel {...props} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function stubApis(source: FakeEventSource) {
  // A constructor mock: vitest 4+ refuses `new` on an arrow implementation.
  vi.stubGlobal("EventSource", vi.fn(function () { return source; }));
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/v1/cycles") && (!init || !init.method || init.method === "GET")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          items: [
            { name: "sample-pytests", description: null, stage_count: 1, equipment: ["pytest"], source: "config" },
            { name: "sample-behave", description: null, stage_count: 1, equipment: ["behave"], source: "config" }
          ],
          config_path: "/repo/testosterone.yaml"
        })
      });
    }
    if (url.includes("/executions") && init?.method === "POST") {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          execution_id: "cyc-exec-1",
          status: "queued",
          events_url: "http://localhost:8000/api/v1/cycle-executions/cyc-exec-1/events",
          summary_url: "http://localhost:8000/api/v1/cycle-executions/cyc-exec-1"
        })
      });
    }
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("RunPanel", () => {
  it("run button submits via click, streams stages and celebrates success", async () => {
    const source = new FakeEventSource();
    stubApis(source);
    renderPanel();

    // Cycle dropdown is fed from the API — no free-text guessing.
    await waitFor(() => expect(screen.getByRole("option", { name: /sample-pytests/ })).toBeInTheDocument());

    // Regression check for the reported non-firing submit: plain click must start the run.
    fireEvent.click(screen.getByRole("button", { name: "Run cycle" }));
    await waitFor(() => expect(screen.getByText(/execution cyc-exec-1/)).toBeInTheDocument());

    source.emit("plan_started", { event: "plan_started", plan: "sample-pytests", stage_count: 1 });
    source.emit("stage_started", { event: "stage_started", stage: "pytest-sample", framework: "pytest", index: 0, count: 1 });
    source.emit("stage_finished", {
      event: "stage_finished",
      stage: "pytest-sample",
      framework: "pytest",
      returncode: 0,
      duration_s: 3.2,
      log_path: null,
      timed_out: false
    });
    source.emit("plan_finished", {
      event: "plan_finished",
      plan: "sample-pytests",
      aggregate_returncode: 0,
      exit_code: 0,
      duration_s: 3.4
    });

    await waitFor(() => expect(screen.getByTestId("run-outcome")).toHaveTextContent("Cycle passed"));
    expect(screen.getByText("pytest-sample")).toBeInTheDocument();
  });

  it("shows the defeated mascot when the plan fails", async () => {
    const source = new FakeEventSource();
    stubApis(source);
    renderPanel({ initialCycle: "sample-pytests", lockCycle: true });

    fireEvent.click(screen.getByRole("button", { name: "Run cycle" }));
    await waitFor(() => expect(screen.getByText(/execution cyc-exec-1/)).toBeInTheDocument());

    source.emit("plan_finished", {
      event: "plan_finished",
      plan: "sample-pytests",
      aggregate_returncode: 1,
      exit_code: 1,
      duration_s: 2.1
    });

    await waitFor(() => expect(screen.getByTestId("run-outcome")).toHaveTextContent("Cycle failed"));
    expect(screen.getByTestId("run-outcome")).toHaveTextContent("Exit code 1");
  });

  it("maps advanced options into the execution payload", async () => {
    const source = new FakeEventSource();
    const fetchMock = stubApis(source);
    renderPanel({ initialCycle: "sample-pytests", lockCycle: true });

    fireEvent.click(screen.getByRole("button", { name: /Advanced options/ }));
    fireEvent.change(screen.getByLabelText("Workers override"), { target: { value: "8" } });
    fireEvent.click(screen.getByLabelText("allure"));
    fireEvent.click(screen.getByLabelText("Fail fast"));
    fireEvent.change(screen.getByLabelText("Artifacts root"), { target: { value: "custom-artifacts" } });

    fireEvent.click(screen.getByRole("button", { name: "Run cycle" }));

    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
      expect(post).toBeDefined();
      const [url, init] = post as [string, RequestInit];
      expect(String(url)).toContain("/api/v1/cycles/sample-pytests/executions");
      const body = JSON.parse(String(init.body));
      expect(body).toMatchObject({
        stream: true,
        persist: true,
        fail_fast: true,
        force: false,
        report_db: true,
        async_report_db: false,
        workers_override: 8,
        reporter_override: ["allure"],
        artifacts_root: "custom-artifacts"
      });
    });
  });

  it("sends a chosen set of tests and offers to run every test instead", async () => {
    const source = new FakeEventSource();
    const fetchMock = stubApis(source);
    const clear = vi.fn();
    renderPanel({
      initialCycle: "sample-pytests",
      lockCycle: true,
      selection: { rerunFailed: "run-42", label: "the failing tests of run run-42" },
      onClearSelection: clear
    });

    expect(screen.getByRole("heading", { name: "Run chosen tests" })).toBeInTheDocument();
    expect(screen.getByText("Runs only the failing tests of run run-42.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Run every test instead" }));
    expect(clear).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole("button", { name: "Run cycle" }));
    await waitFor(() => {
      const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
      expect(post).toBeDefined();
      const body = JSON.parse(String((post as [string, RequestInit])[1].body));
      expect(body).toMatchObject({ rerun_failed: "run-42" });
      expect(body.tests).toBeUndefined();
    });
  });
});
