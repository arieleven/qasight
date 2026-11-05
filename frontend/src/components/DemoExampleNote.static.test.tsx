import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AskPage } from "../features/ask/AskPage";
import { ReleasesPage } from "../features/releases/ReleasesPage";

vi.mock("../lib/static-backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/static-backend")>()),
  IS_STATIC_BUILD: true,
  loadManifest: async () => ({ examples: ["releases", "ask"] }),
  loadAskExamples: async () => [{ question: "What should I fix first?", answer: "Start with fake-api.", model: "made-up example" }]
}));

function stubFetch(routes: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const key = Object.keys(routes).find((path) => url.includes(path));
      return { ok: Boolean(key), status: key ? 200 : 404, json: async () => (key ? routes[key] : {}) } as Response;
    })
  );
}

function renderPage(page: React.ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{page}</MemoryRouter>
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("made-up examples in the live demo", () => {
  it("shows an example conversation on the Ask page, marked as fake", async () => {
    stubFetch({ "/ai/config/status": { enabled: false, configured: false } });
    renderPage(<AskPage />);
    expect(await screen.findByText("Start with fake-api.")).toBeInTheDocument();
    expect(screen.getByText("What should I fix first?")).toBeInTheDocument();
    expect(screen.getByTestId("ask-example-note")).toHaveTextContent("Fake example for the live demo");
    expect(screen.getByRole("button", { name: "Ask" })).toBeDisabled();
  });

  it("marks the example releases as fake", async () => {
    stubFetch({
      "/releases": {
        items: [{ release: "2.1-example", first_run_at: 1, last_run_at: 2, runs: 2, cycles: 1, total: 10, passed: 9, failed: 1, skipped: 0, pass_rate: 90 }]
      }
    });
    renderPage(<ReleasesPage />);
    expect(await screen.findByText("2.1-example")).toBeInTheDocument();
    expect(await screen.findByTestId("demo-example-note")).toHaveTextContent("made up");
  });
});
