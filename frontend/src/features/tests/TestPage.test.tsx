import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TestPage } from "./TestPage";

const TEST = {
  key: "tests/api.py::test_login",
  name: "test_login",
  full_name: "tests.api.test_login",
  group: "tests.api",
  stage: "api",
  runs: 4,
  passed: 3,
  failed: 1,
  last_status: "failed",
  flaky: true,
  last_duration_ms: 400,
  median_duration_ms: 120,
  slowdown_ms: 280,
  slowdown_pct: 233.3
};

function point(runId: string, status: string, createdAt: number, message = "", flipRate: number | null = null) {
  return { run_id: runId, created_at: createdAt, status, duration_ms: 100, message, branch: "main", flip_rate: flipRate };
}

function renderAt(url: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/cycles/:name/test" element={<TestPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TestPage", () => {
  it("shows the pass rate, last failure and every run", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/quarantine") {
        expect(url.searchParams.get("cycle")).toBe("smoke");
        const items = [{ cycle: "smoke", test: "tests.api.*", is_glob: true, reason: "", owner: "", added_by: "ada", added_at: 1 }];
        return Promise.resolve(new Response(JSON.stringify({ items, release_after: 10 }), { status: 200 }));
      }
      expect(url.pathname).toBe("/api/v1/analytics/test-history");
      expect(url.searchParams.get("key")).toBe("tests/api.py::test_login");
      const failure = point("run-4abcdef0", "failed", 4, "AssertionError: 1 != 2");
      return Promise.resolve(
        new Response(
          JSON.stringify({
            cycle: "smoke",
            test: TEST,
            points: [failure, point("run-3", "passed", 3), point("run-2", "passed", 2), point("run-1", "passed", 1)],
            last_failure: failure,
            last_failure_attachments: [
              { name: "screenshot", content_type: "image/png", kind: "image", url: "history/run-4/a-attachment.png", size_bytes: 70 },
              { name: "trace", content_type: "application/zip", kind: "trace", url: "history/run-4/b-attachment.zip", size_bytes: 2048 }
            ],
            owners: ["@org/api"],
            issue_url: "https://github.com/acme/shop/issues/new?title=Flaky+test"
          }),
          { status: 200 }
        )
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderAt(`/cycles/smoke/test?${new URLSearchParams({ key: TEST.key }).toString()}`);

    // While history loads the page is headed "Test"; the test's own name replaces it.
    expect(await screen.findByRole("heading", { level: 1, name: /test_login/ })).toBeInTheDocument();
    expect(screen.getByText("Passed 3 of 4 runs (75%) in smoke")).toBeInTheDocument();
    expect(screen.getByText("Flaky")).toBeInTheDocument();
    expect(screen.getAllByText("AssertionError: 1 != 2")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Run run-4abc" })).toHaveAttribute("href", "/runs/run-4abcdef0");
    expect(screen.getByText(/Median 120 ms, and 280 ms slower over the last runs \(\+233.3%\)/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Runs (4)" })).toBeInTheDocument();
    // The duration chart reads as text, too, and every bar opens its run.
    const bars = screen.getByRole("list", { name: /^Duration per run, oldest first: 100 ms passed, .*, 100 ms failed$/ });
    expect(within(bars).getAllByRole("link")).toHaveLength(4);
    expect(within(bars).getByRole("link", { name: /100 ms, failed, open run run-4abc/ })).toHaveAttribute("href", "/runs/run-4abcdef0");
    // Outcomes use the same pill as runs do, with the run's short id beside its time.
    expect(screen.getByText("Last run:")).toBeInTheDocument();
    expect(screen.getAllByText("Failed")).toHaveLength(2);
    expect(screen.getAllByText("Passed")).toHaveLength(3);
    expect(screen.getByText("run-3")).toBeInTheDocument();
    expect(screen.getByText("@org/api")).toBeInTheDocument();
    // What the failing run kept: the screenshot in place, the trace to download.
    expect(screen.getByRole("img", { name: "screenshot from the failing run" })).toHaveAttribute(
      "src",
      expect.stringMatching(/\/history\/run-4\/a-attachment\.png$/)
    );
    expect(screen.getByRole("link", { name: "Download trace" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/history\/run-4\/b-attachment\.zip$/)
    );
    expect(screen.getByText("2 KB")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open an issue" })).toHaveAttribute(
      "href",
      "https://github.com/acme/shop/issues/new?title=Flaky+test"
    );
    // Quarantined by a glob on its full name: the badge, and no Release for one test of it.
    expect(await screen.findByText("Quarantined")).toHaveAttribute("title", "matched by tests.api.* · by ada");
    expect(screen.queryByRole("button", { name: /Release/ })).not.toBeInTheDocument();
  });

  it("offers to quarantine a test the server's list doesn't have", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = new URL(String(input), "http://localhost");
        const body =
          url.pathname === "/api/v1/quarantine"
            ? { items: [], release_after: 10 }
            : { cycle: "smoke", test: TEST, points: [], last_failure: null, owners: [], issue_url: null };
        return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
      })
    );
    renderAt(`/cycles/smoke/test?${new URLSearchParams({ key: TEST.key }).toString()}`);
    expect(await screen.findByRole("button", { name: "Quarantine test_login" })).toBeInTheDocument();
  });

  it("shows how flaky the test has been over time", async () => {
    // Newest first, as the API sends them: rates 0.25 five runs ago, 0.75 now.
    const rates = [0.75, 0.5, 0.5, 0.25, 0.25, 0.25, 0, null];
    const points = rates.map((rate, i) => point(`run-${8 - i}`, i % 2 ? "failed" : "passed", 8 - i, "", rate));
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ cycle: "smoke", test: TEST, points, last_failure: null, owners: [], issue_url: null }), {
            status: 200
          })
        )
      )
    );
    renderAt(`/cycles/smoke/test?${new URLSearchParams({ key: TEST.key }).toString()}`);

    expect(await screen.findByRole("heading", { name: "Flakiness over time" })).toBeInTheDocument();
    expect(screen.getByTestId("flip-rate-summary")).toHaveTextContent(
      "Outcome changed in 75% of the last 5 runs, up from 25% five runs before."
    );
    expect(screen.getByRole("img", { name: /^Flip rate per run, oldest first: n\/a, 0%, 25%/ })).toBeInTheDocument();
  });

  it("explains a test that isn't in the recent runs", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 404 }))));
    renderAt("/cycles/smoke/test?key=gone");
    expect(await screen.findByText(/Couldn't find this test in the recent runs of smoke/)).toBeInTheDocument();
  });
});
