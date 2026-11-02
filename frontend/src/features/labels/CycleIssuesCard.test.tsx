import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "../../lib/api-client";
import { CycleIssuesCard } from "./CycleIssuesCard";

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CycleIssuesCard cycle="api" />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

afterEach(() => vi.restoreAllMocks());

describe("CycleIssuesCard", () => {
  it("lists issues with red tests first and links their failing tests", async () => {
    vi.spyOn(apiClient, "getCycleIssues").mockResolvedValue({
      cycle: "api",
      runs_considered: 5,
      linked_tests: 3,
      total_tests: 10,
      items: [
        {
          issue: "PAY-7",
          url: "https://jira.example/browse/PAY-7",
          tests: 2,
          failing: 1,
          flaky: 0,
          state: "failing",
          examples: [
            { key: "k1", name: "charges card", last_status: "failed", flaky: false },
            { key: "k2", name: "refunds", last_status: "passed", flaky: false }
          ]
        },
        { issue: "PAY-8", url: null, tests: 1, failing: 0, flaky: 0, state: "passing", examples: [] }
      ]
    });
    renderCard();
    expect(await screen.findByText("1 issue has red tests")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "PAY-7" })).toHaveAttribute("href", "https://jira.example/browse/PAY-7");
    expect(screen.getByRole("link", { name: "charges card" })).toHaveAttribute("href", "/cycles/api/test?key=k1");
    expect(screen.queryByRole("link", { name: "refunds" })).not.toBeInTheDocument();
    expect(screen.getByText("PAY-8")).toBeInTheDocument();
  });

  it("is hidden when no test points at an issue", async () => {
    const spy = vi
      .spyOn(apiClient, "getCycleIssues")
      .mockResolvedValue({ cycle: "api", runs_considered: 1, linked_tests: 0, total_tests: 3, items: [] });
    const { container } = renderCard();
    await vi.waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
