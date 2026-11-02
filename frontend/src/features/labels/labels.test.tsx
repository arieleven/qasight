import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CycleTestsCard } from "../cycles/CycleTestsCard";
import { CycleLabelsCard } from "./CycleLabelsCard";
import { TestLabels } from "./TestLabels";
import { hasLabel, labelText } from "./labels";

function wrap(node: React.ReactNode, path = "/cycles/smoke") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{node}</MemoryRouter>
    </QueryClientProvider>
  );
}

function respond(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status: 200 })))
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("label helpers", () => {
  it("matches name:value exactly or a bare value, ignoring case", () => {
    const labels = [
      { name: "feature", value: "Checkout" },
      { name: "tag", value: "smoke" }
    ];
    expect(hasLabel(labels, "feature:checkout")).toBe(true);
    expect(hasLabel(labels, "SMOKE")).toBe(true);
    expect(hasLabel(labels, "story:checkout")).toBe(false);
    expect(hasLabel(undefined, "smoke")).toBe(false);
    expect(labelText({ name: "tag", value: "smoke" })).toBe("smoke");
    expect(labelText({ name: "severity", value: "critical" })).toBe("severity: critical");
  });
});

describe("TestLabels", () => {
  it("links each label to the cycle's filtered tests and opens issue links in a new tab", () => {
    wrap(
      <TestLabels
        cycle="smoke"
        labels={[{ name: "feature", value: "Checkout" }]}
        links={[{ name: "PROJ-12", url: "https://jira.example.com/browse/PROJ-12", type: "issue" }]}
      />
    );
    expect(screen.getByRole("link", { name: "feature: Checkout" })).toHaveAttribute(
      "href",
      "/cycles/smoke?label=feature%3ACheckout#tests"
    );
    const issue = screen.getByRole("link", { name: "Issue PROJ-12" });
    expect(issue).toHaveAttribute("href", "https://jira.example.com/browse/PROJ-12");
    expect(issue).toHaveAttribute("target", "_blank");
  });

  it("renders nothing without labels or links", () => {
    const { container } = wrap(<TestLabels labels={[]} links={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("CycleLabelsCard", () => {
  it("groups counts by label name and links each value to its tests", async () => {
    respond({
      cycle: "smoke",
      runs_considered: 4,
      labelled_tests: 3,
      total_tests: 5,
      items: [
        { name: "feature", value: "Checkout", tests: 2, failing: 1, flaky: 0, filter: "feature:Checkout" },
        { name: "feature", value: "Search", tests: 1, failing: 0, flaky: 1, filter: "feature:Search" },
        { name: "tag", value: "smoke", tests: 3, failing: 1, flaky: 0, filter: "tag:smoke" }
      ]
    });
    wrap(<CycleLabelsCard cycle="smoke" />);
    expect(await screen.findByRole("heading", { name: "Features" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Tags" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Checkout" })).toHaveAttribute(
      "href",
      "/cycles/smoke?label=feature%3ACheckout#tests"
    );
    expect(screen.getAllByText(/, 1 failing/, { selector: "span" })).toHaveLength(2);
    expect(screen.getByText(/, 1 flaky/, { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/3 of 5 tests carry a label/)).toBeInTheDocument();
  });

  it("is hidden when no test has a label", async () => {
    respond({ cycle: "smoke", runs_considered: 4, labelled_tests: 0, total_tests: 5, items: [] });
    const { container } = wrap(<CycleLabelsCard cycle="smoke" />);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(container).toBeEmptyDOMElement();
  });
});

describe("CycleTestsCard with ?label=", () => {
  it("lists only the tests that carry the label", async () => {
    const base = {
      full_name: "t",
      group: "tests",
      stage: "api",
      runs: 2,
      passed: 2,
      failed: 0,
      last_status: "passed",
      flaky: false,
      last_duration_ms: 5,
      median_duration_ms: 5,
      slowdown_ms: null,
      slowdown_pct: null,
      links: []
    };
    respond({
      cycle: "smoke",
      runs_considered: 2,
      items: [
        { ...base, key: "a", name: "test_pay", labels: [{ name: "feature", value: "Checkout" }] },
        { ...base, key: "b", name: "test_find", labels: [{ name: "feature", value: "Search" }] }
      ]
    });
    wrap(<CycleTestsCard cycle="smoke" />, "/cycles/smoke?label=feature:Checkout");
    const list = await screen.findByRole("list", { name: "Tests with this label" });
    expect(list).toHaveTextContent("test_pay");
    expect(list).not.toHaveTextContent("test_find");
    expect(screen.getByRole("heading", { name: '1 test labelled "feature:Checkout"' })).toBeInTheDocument();
  });
});
