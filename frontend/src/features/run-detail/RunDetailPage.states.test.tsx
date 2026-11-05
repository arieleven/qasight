import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RunDetailPage } from "./RunDetailPage";

type RouteMap = Record<string, unknown>;

const RUN = {
  run_id: "run-1",
  test_kind: "cycle",
  cycle: "smoke",
  returncode: 1,
  created_at: 1,
  started_at: 1,
  finished_at: 2,
  wall_duration_ms: 1234,
  health_pct: 50,
  stage_health: [
    { name: "unit", framework: "pytest", total_tests: 4, passed: 4, health_pct: 100 },
    { name: "ui", framework: "behavex", total_tests: 2, passed: 0, health_pct: 0 }
  ]
};

const REPORTS = { static_links: { "Allure report": "history/run-1/allure/index.html" }, artifact_links: ["artifacts/smoke/run.log"] };
const FAILURES = {
  run_id: "run-1",
  runs_considered: 3,
  new_failures: 1,
  tests: [
    {
      key: "k1",
      name: "test_login",
      full_name: "tests.ui.test_login",
      group: "tests.ui",
      stage: "ui",
      status: "failed",
      message: "ConnectionRefusedError: [Errno 111]",
      duration_ms: 1500,
      failing_runs: 1,
      failing_since_run_id: "run-1",
      flaky: true,
      new: true
    },
    {
      key: "k2",
      name: "test_logout",
      full_name: "tests.ui.test_logout",
      group: "tests.ui",
      stage: "ui",
      status: "failed",
      message: "ConnectionRefusedError: [Errno 111]",
      duration_ms: 20,
      failing_runs: 3,
      failing_since_run_id: "run-0abcdef",
      flaky: false,
      new: false
    }
  ],
  groups: [{ message: "ConnectionRefusedError: [Errno 111]", count: 2, keys: ["k1", "k2"] }],
  baseline_run_id: "run-0",
  baseline_branch: null,
  rerun_commands: []
};
const PYRAMID = { unit: 4, integration: 0, e2e: 2, shape: "pyramid", message: "Healthy" };

function summary(overrides: Record<string, unknown>) {
  return {
    schema_version: "v1",
    run_id: "run-1",
    status: "not_available",
    summary_text: null,
    confidence: null,
    limitations: [],
    provider: null,
    model: null,
    generated_at: null,
    context_stats: {},
    error_code: "summary_not_available",
    ...overrides
  };
}

// Answers GETs from `routes` (by path suffix) and lets a test override POSTs.
function stubApi(routes: RouteMap, onPost?: (url: string) => Response | Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === "POST" && onPost) return Promise.resolve(onPost(url));
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((suffix) => url.endsWith(suffix));
    if (key === undefined) return Promise.resolve(new Response("{}", { status: 404 }));
    const body = routes[key];
    if (body instanceof Response) return Promise.resolve(body);
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderAt(path = "/runs/run-1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function defaultRoutes(overrides: RouteMap = {}): RouteMap {
  return {
    "/api/v1/runs/run-1": { run: RUN },
    "/api/v1/runs/run-1/reports": REPORTS,
    "/api/v1/runs/run-1/pyramid": PYRAMID,
    "/api/v1/runs/run-1/ai-summary": summary({}),
    "/api/v1/runs/run-1/failures": FAILURES,
    ...overrides
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear(); // collapsible cards remember whether they were open
});

/** The Details card starts closed; open it like a viewer would. */
async function openDetails() {
  fireEvent.click(await screen.findByRole("button", { name: /^Details/ }));
}

describe("RunDetailPage states", () => {
  it("shows an error when the run cannot be loaded", async () => {
    stubApi(defaultRoutes({ "/api/v1/runs/run-1": new Response("{}", { status: 500 }) }));
    renderAt();
    expect(await screen.findByText(/Couldn't load this run/)).toBeInTheDocument();
  });

  it("lists stage health, report links and artifacts on demand", async () => {
    stubApi(defaultRoutes());
    renderAt();

    expect(await screen.findByText("Allure report")).toHaveAttribute(
      "href",
      expect.stringMatching(/\/history\/run-1\/allure\/index\.html$/)
    );
    expect(screen.getByText("4/4")).toBeInTheDocument();
    expect(screen.getByText("0/2")).toBeInTheDocument();
    await openDetails();
    expect(screen.getByText("1.2 s")).toBeInTheDocument();

    expect(screen.queryByText("artifacts/smoke/run.log")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Artifacts/ }));
    expect(screen.getByText("artifacts/smoke/run.log")).toBeInTheDocument();
  });

  it("links each stage's log and the artifacts when the API serves them", async () => {
    stubApi(
      defaultRoutes({
        "/api/v1/runs/run-1": { run: { ...RUN, total_tests: 0, failed: 0, stage_health: [{ name: "unit", framework: "pytest", total_tests: 0, passed: 0, health_pct: null }] } },
        "/api/v1/runs/run-1/reports": {
          static_links: {},
          artifact_links: ["events.ndjson", "unit/run.log"],
          artifacts_url: "history/run-1/artifacts"
        }
      })
    );
    renderAt();

    // A stage that crashed before its tests: the log is the only place the error is.
    expect(await screen.findByRole("link", { name: "Log of unit" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/history\/run-1\/artifacts\/unit\/run\.log$/)
    );
    expect(screen.getByText(/Failed before any test result was recorded/)).toBeInTheDocument();
    await openDetails();
    expect(screen.getByText("No tests ran")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Artifacts/ }));
    expect(screen.getByRole("link", { name: "events.ndjson" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/history\/run-1\/artifacts\/events\.ndjson$/)
    );
  });

  it("names a stage that died of a stack overflow", async () => {
    const detail = "Stack overflow (Go): runtime: goroutine stack exceeds 1000000000-byte limit";
    stubApi(
      defaultRoutes({
        "/api/v1/runs/run-1": {
          run: {
            ...RUN,
            stage_health: [{ ...RUN.stage_health[1], crash_kind: "stack_overflow", crash_detail: detail }]
          }
        }
      })
    );
    renderAt();

    expect(await screen.findByTestId("stage-crash")).toHaveTextContent(detail);
    expect(screen.getAllByText("Stack overflow").length).toBeGreaterThan(0);
  });

  it("shows empty states when a run has no reports or stage breakdown", async () => {
    stubApi(
      defaultRoutes({
        "/api/v1/runs/run-1": { run: { ...RUN, stage_health: [] } },
        "/api/v1/runs/run-1/reports": { static_links: {}, artifact_links: [] },
        "/api/v1/runs/run-1/pyramid": new Response("{}", { status: 500 })
      })
    );
    renderAt();
    await openDetails();
    expect(await screen.findByText("No reports for this run.")).toBeInTheDocument();
    expect(screen.getByText("Per-stage breakdown not available for this run.")).toBeInTheDocument();
    expect(await screen.findByText("Couldn't load the test mix.")).toBeInTheDocument();
  });

  it("shows the test mix only when the run has two or more tiers", async () => {
    stubApi(defaultRoutes());
    const { unmount } = renderAt();
    expect(await screen.findByRole("heading", { name: "Test mix" })).toBeInTheDocument();
    unmount();

    stubApi(defaultRoutes({ "/api/v1/runs/run-1/pyramid": { ...PYRAMID, unit: 0, integration: 0, e2e: 6, shape: "top_heavy" } }));
    renderAt();
    await screen.findByRole("heading", { name: "Reports" });
    expect(screen.queryByRole("heading", { name: "Test mix" })).not.toBeInTheDocument();
  });

  it("leaves failing tests and the AI summary off a passed run", async () => {
    stubApi(defaultRoutes({ "/api/v1/runs/run-1": { run: { ...RUN, returncode: 0, total_tests: 6, failed: 0 } } }));
    renderAt();
    expect(await screen.findByText(/All 6 tests passed in 1.2 s/)).toBeInTheDocument();
    expect(screen.queryByText("Failing tests")).not.toBeInTheDocument();
    expect(screen.queryByText("AI failure summary")).not.toBeInTheDocument();
  });

  it("puts the failing tests first, with their message, streak and shared cause", async () => {
    stubApi(defaultRoutes({ "/api/v1/runs/run-1": { run: { ...RUN, total_tests: 6, failed: 2 } } }));
    renderAt();
    expect(await screen.findByText("test_login")).toBeInTheDocument();
    expect(screen.getByText(/2 tests of 6 failed in 1.2 s/)).toBeInTheDocument();
    expect(screen.getByText(/2 tests failed/)).toHaveTextContent("2 tests failed, 1 new in this run, 1 already failing before.");
    expect(screen.getByText("New")).toBeInTheDocument();
    expect(screen.getByText("Failing for 3 runs")).toHaveAttribute("title", "Failing since run run-0abc");
    expect(screen.getByText("Flaky")).toBeInTheDocument();
    expect(screen.getByText("Same cause")).toBeInTheDocument();
    expect(screen.getByText("2 tests:")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See this cycle's flaky tests" })).toHaveAttribute("href", "/cycles/smoke");
    // The failing tests come before the stage list.
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings.indexOf("Failing tests")).toBeLessThan(headings.indexOf("Stages"));
  });

  it("judges a pull request's failures against its target branch", async () => {
    stubApi(
      defaultRoutes({
        "/api/v1/runs/run-1": {
          run: { ...RUN, branch: "feat/login", base_branch: "main", commit: "abc1234def", repository: "acme/shop" }
        },
        "/api/v1/runs/run-1/failures": {
          ...FAILURES,
          baseline_branch: "main",
          tests: [FAILURES.tests[0], { ...FAILURES.tests[1], failing_runs: 1 }]
        }
      })
    );
    renderAt();
    expect(await screen.findByText(/2 tests failed/)).toHaveTextContent(
      "2 tests failed, 1 new since main, 1 already failing on main."
    );
    expect(screen.getByText("New")).toHaveAttribute("title", "Not failing on main");
    expect(screen.getByText("Also failing on main")).toBeInTheDocument();
    // The header says what the run tested.
    expect(screen.getByText("feat/login")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "abc1234" })).toHaveAttribute(
      "href",
      "https://github.com/acme/shop/commit/abc1234def"
    );
  });

  it("offers a command that reruns just the failing tests", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const command = "pytest tests/ui/test_login.py::test_login tests/ui/test_logout.py::test_logout";
    stubApi(defaultRoutes({ "/api/v1/runs/run-1/failures": { ...FAILURES, rerun_commands: [command] } }));
    renderAt();
    expect(await screen.findByText("Rerun these tests")).toBeInTheDocument();
    expect(screen.getByText(command)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy rerun command" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(command));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("explains a failed run with no failing test", async () => {
    stubApi(defaultRoutes({ "/api/v1/runs/run-1/failures": { ...FAILURES, tests: [], groups: [], new_failures: 0 } }));
    renderAt();
    expect(await screen.findByText(/No individual test failed/)).toBeInTheDocument();
  });

  it("renders the labelled summary sections", async () => {
    stubApi(
      defaultRoutes({
        "/api/v1/runs/run-1/ai-summary": summary({
          status: "available",
          error_code: null,
          summary_text: "What failed: ui login\nLikely cause: stale selector\nNext steps: update the page object",
          confidence: "high",
          model: "gemini-2.5-flash"
        })
      })
    );
    renderAt();
    expect(await screen.findByText("Likely cause:")).toBeInTheDocument();
    expect(screen.getByText(/stale selector/)).toBeInTheDocument();
    expect(screen.getByText(/Confidence: high \| Model: gemini-2.5-flash/)).toBeInTheDocument();
  });

  it("links to AI settings when the provider is misconfigured", async () => {
    stubApi(defaultRoutes({ "/api/v1/runs/run-1/ai-summary": summary({ error_code: "provider_misconfigured" }) }));
    renderAt();
    expect(await screen.findByText(/the AI provider has no API key/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open AI settings" })).toHaveAttribute("href", "/settings/ai");
  });

  it("shows the API's message when generating a summary fails", async () => {
    stubApi(defaultRoutes(), () =>
      new Response(JSON.stringify({ error: { code: "provider_timeout", message: "AI provider timed out." } }), {
        status: 503
      })
    );
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: "Generate AI summary" }));
    await waitFor(() => expect(screen.getByText("AI provider timed out.")).toBeInTheDocument());
  });
});
