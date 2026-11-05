import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AiReviewCard } from "./AiReviewCard";

vi.mock("../../lib/static-backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/static-backend")>()),
  IS_STATIC_BUILD: true
}));

describe("AiReviewCard in the read-only demo", () => {
  it("lists the checks but explains the review can't run here", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.includes("/skills")
          ? { items: [{ id: "debt", name: "Test debt", category: "Maintenance", tagline: "t", summary: "s", checks: [], reads: [], default_on: true }] }
          : url.includes("/ai-review")
            ? { scope: null, status: "not_generated", skills: [], stale: false, context_stats: {} }
            : { enabled: true, configured: true };
        return { ok: true, status: 200, json: async () => body } as Response;
      })
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <AiReviewCard />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByTestId("skill-debt")).toBeInTheDocument();
    expect(screen.getByTestId("ai-review-demo-note")).toHaveTextContent("read-only");
    expect(screen.queryByText(/AI is not set up yet/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run AI review" })).toBeDisabled();
  });

  it("marks a made-up example review as fake", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.includes("/skills")
          ? { items: [{ id: "debt", name: "Test debt", category: "Maintenance", tagline: "t", summary: "s", checks: [], reads: [], default_on: true }] }
          : url.includes("/ai-review")
            ? {
                scope: null,
                status: "available",
                score: 80,
                grade: "B",
                summary: "Example review",
                skills: [{ skill_id: "debt", name: "Test debt", score: 80, summary: "Long-red tests", findings: [] }],
                provider: "demo-example",
                model: "made-up example, not a real AI answer",
                stale: false,
                context_stats: {}
              }
            : { enabled: false, configured: false };
        return { ok: true, status: 200, json: async () => body } as Response;
      })
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <AiReviewCard />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByTestId("ai-review-result")).toHaveTextContent("Example review");
    expect(screen.getByTestId("ai-review-example-note")).toHaveTextContent("Fake example for the live demo");
  });
});
