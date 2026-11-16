import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FailingTestItem, RunFailuresResponse, TriageItem } from "../../lib/api-client";
import { FailingTestsCard } from "./FailingTestsCard";

const DB_MESSAGE = "ConnectionRefusedError: port 5433";

function failing(
  key: string,
  message: string,
  triage: TriageItem | null = null,
  crash_kind: string | null = null
): FailingTestItem {
  return {
    key,
    name: key,
    full_name: `tests.${key}`,
    group: "tests",
    stage: "api",
    status: "failed",
    message,
    duration_ms: 10,
    failing_runs: 1,
    failing_since_run_id: "run-2",
    flaky: false,
    new: true,
    triage,
    suggested_cause: null,
    suggested_from: null,
    suggested_confidence: null,
    suggested_example: null,
    crash_kind,
    labels: [],
    links: []
  };
}

const ENVIRONMENT: TriageItem = {
  cycle: "api",
  signature: "connectionrefusederror: port …",
  cause: "environment",
  note: "DB container starts late",
  example_message: "ConnectionRefusedError: port 5432",
  run_id: "run-1-abcdef",
  test_key: "test_db",
  marked_by: "ada",
  marked_at: 1_700_000_000
};

function failures(tests: FailingTestItem[]): RunFailuresResponse {
  return {
    run_id: "run-2",
    runs_considered: 2,
    new_failures: tests.length,
    baseline_run_id: null,
    baseline_branch: null,
    rerun_commands: [],
    tests,
    groups: []
  };
}

function renderCard() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <FailingTestsCard runId="run-2" cycle="api" />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("FailingTestsCard triage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an inherited cause and marks a new one", async () => {
    let current = failures([failing("test_db", DB_MESSAGE, ENVIRONMENT), failing("test_math", "AssertionError")]);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/triage") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        const marked = { ...ENVIRONMENT, cause: body.cause, note: body.note, run_id: "run-2", test_key: "test_math" };
        current = failures([current.tests[0], failing("test_math", "AssertionError", marked)]);
        return Promise.resolve({ ok: true, status: 200, json: async () => marked });
      }
      if (url.endsWith("/api/v1/runs/run-2/failures")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => current });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderCard();

    const inherited = await screen.findByTestId("triage");
    expect(within(inherited).getByText("Cause: Environment or infrastructure")).toBeInTheDocument();
    expect(within(inherited).getByText("DB container starts late")).toBeInTheDocument();
    expect(within(inherited).getByText(/From an earlier failure with the same message in run run-1-ab/)).toBeInTheDocument();
    expect(screen.getByText(/1 with a known cause/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Mark the cause" }));
    fireEvent.change(screen.getByLabelText("Cause"), { target: { value: "test_bug" } });
    fireEvent.change(screen.getByLabelText("Note (optional)"), { target: { value: "assert is wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Save cause" }));

    await waitFor(() => expect(screen.getAllByTestId("triage")).toHaveLength(2));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({
      cycle: "api",
      message: "AssertionError",
      cause: "test_bug",
      note: "assert is wrong",
      run_id: "run-2",
      test_key: "test_math"
    });
    expect(screen.getByText("Cause: Bug in the test")).toBeInTheDocument();
    expect(screen.getByText(/all with a known cause/)).toBeInTheDocument();
  });
});

describe("FailingTestsCard suggested cause", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("accepts a suggested cause in one click", async () => {
    let current = failures([{ ...failing("test_db", DB_MESSAGE), suggested_cause: "environment" }]);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/triage") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        const marked = { ...ENVIRONMENT, cause: body.cause, note: body.note, run_id: "run-2" };
        current = failures([failing("test_db", DB_MESSAGE, marked)]);
        return Promise.resolve({ ok: true, status: 200, json: async () => marked });
      }
      if (url.endsWith("/api/v1/runs/run-2/failures")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => current });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderCard();

    const hint = await screen.findByTestId("suggested-cause");
    expect(within(hint).getByText("Looks like: Environment or infrastructure")).toBeInTheDocument();
    fireEvent.click(within(hint).getByRole("button", { name: "Accept" }));

    await waitFor(() => expect(screen.getByTestId("triage")).toBeInTheDocument());
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body)).cause).toBe("environment");
  });
});

describe("FailingTestsCard learned cause and Jira", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows how sure a learned suggestion is and files the failure in Jira", async () => {
    let current = failures([
      {
        ...failing("test_total", "AssertionError: total 3 != 4"),
        suggested_cause: "product_bug",
        suggested_from: "similar",
        suggested_confidence: 0.72,
        suggested_example: "AssertionError: total 9 != 10 after discount"
      }
    ]);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/jira")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ enabled: true, project: "SHOP", url: "https://j" }) });
      }
      if (url.endsWith("/api/v1/jira/issues") && init?.method === "POST") {
        const marked = { ...ENVIRONMENT, cause: "product_bug" as const, note: "SHOP-12 https://j/browse/SHOP-12", run_id: "run-2" };
        current = failures([failing("test_total", "AssertionError: total 3 != 4", marked)]);
        return Promise.resolve({
          ok: true,
          status: 201,
          json: async () => ({ key: "SHOP-12", url: "https://j/browse/SHOP-12", cause_marked: true })
        });
      }
      if (url.endsWith("/api/v1/runs/run-2/failures")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => current });
      }
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderCard();

    const hint = await screen.findByTestId("suggested-cause");
    expect(within(hint).getByText(/72% sure, like a failure your team marked/)).toBeInTheDocument();
    fireEvent.click(await within(hint).findByRole("button", { name: "File in Jira (SHOP)" }));

    await waitFor(() => expect(screen.getByTestId("triage")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "https://j/browse/SHOP-12" })).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/api/v1/jira/issues"));
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ cycle: "api", test: "test_total", run_id: "run-2" });
  });
});

describe("FailingTestsCard crash detection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("badges a stack overflow and a crash, and nothing on a plain failure", async () => {
    const data = failures([
      failing("test_parse", "RecursionError: maximum recursion depth exceeded", null, "stack_overflow"),
      failing("test_native", "Fatal Python error: Segmentation fault", null, "crash"),
      failing("test_math", "AssertionError")
    ]);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, status: 200, json: async () => data }))
    );

    renderCard();

    expect(await screen.findByText("Stack overflow")).toBeInTheDocument();
    expect(screen.getByText("Crashed")).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    const plain = rows.find((row) => within(row).queryByText("test_math"));
    expect(plain && within(plain).queryByText(/Stack overflow|Crashed/)).toBeNull();
  });
});
