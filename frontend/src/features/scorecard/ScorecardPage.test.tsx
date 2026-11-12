import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ScorecardPage, gradeFor, rollUp } from "./ScorecardPage";

function tool(id: string, name: string, category: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    name,
    category,
    category_name: category === "security" ? "Security" : "Code quality",
    tagline: `${name} tagline`,
    how: "Starts at 100.",
    homepage: "https://example.com",
    kind: "repo",
    report: "JSON",
    languages: [],
    default_on: true,
    enabled: true,
    installed: true,
    install: `pip install ${id}`,
    ...extra
  };
}

const tools = { items: [tool("bandit", "Bandit", "security"), tool("zap", "OWASP ZAP", "security", { kind: "app" }), tool("ruff", "Ruff", "code")] };

function result(id: string, name: string, category: string, score: number, previous: number | null) {
  return { tool: id, name, category, enabled: true, score, previous_score: previous, summary: `${name} said ${score}`, metrics: {}, counts: {}, findings: [{ severity: "high", title: "B602", detail: "shell=True", location: "a.py:3" }], recorded_at: 1, source: "scan", commit_sha: "abcdef1234" };
}

const card = {
  status: "available",
  project: "qasight",
  version: "1.6.0",
  score: 75,
  grade: "C",
  previous_version: "1.5.0",
  previous_score: 90,
  recorded_at: 1,
  commit_sha: "abcdef1234",
  categories: [
    { key: "security", name: "Security", score: 50, previous_score: 80, tools: ["bandit"] },
    { key: "code", name: "Code quality", score: 100, previous_score: 100, tools: ["ruff"] }
  ],
  tools: [result("bandit", "Bandit", "security", 50, 80), result("ruff", "Ruff", "code", 100, 100)],
  versions: [
    { version: "1.6.0", first_at: 2, last_at: 2, score: 75, grade: "C", tools: 2 },
    { version: "1.5.0", first_at: 1, last_at: 1, score: 90, grade: "A", tools: 2 }
  ]
};

function stub(scorecard: unknown = card) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    let body: unknown = {};
    if (url.includes("/scorecard/tools/")) body = { ...tool("bandit", "Bandit", "security"), enabled: JSON.parse(String(init?.body)).enabled };
    else if (url.includes("/scorecard/tools")) body = tools;
    else if (url.includes("/scorecard/projects")) body = { items: [{ project: "qasight", versions: card.versions }] };
    else if (url.includes("/scorecard/scans")) body = { id: "s1", target: "x", state: "running", tools: [{ tool: "ruff", status: "running", score: null, message: "" }], skipped: { zap: "needs a running app (--url)" }, languages: ["python"], message: "Running 1 tool(s)", started_at: 1 };
    else if (url.includes("/scorecard")) body = scorecard;
    return { ok: true, status: 200, json: async () => body } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/scorecard"]}>
        <ScorecardPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return fetchMock;
}

describe("rollUp", () => {
  it("averages each category, then the categories, leaving switched-off tools out", () => {
    const items = card.tools;
    expect(rollUp(items, () => true, (t) => t.score).score).toBe(75);
    expect(rollUp(items, (id) => id !== "bandit", (t) => t.score).score).toBe(100);
    expect(rollUp(items, () => true, (t) => t.previous_score).score).toBe(90);
    expect(gradeFor(75)).toBe("C");
    expect(gradeFor(null)).toBeNull();
  });
});

describe("ScorecardPage", () => {
  it("shows the version's score, the change since the last version and each version", async () => {
    stub();
    await waitFor(() => expect(screen.getByRole("img", { name: "Release score: 75 out of 100, grade C" })).toBeInTheDocument());
    expect(screen.getByText(/vs 1.5.0: 90/)).toBeInTheDocument();
    expect(within(screen.getByTestId("scorecard-versions")).getByRole("button", { name: "1.5.0" })).toBeInTheDocument();
  });

  it("lists every tool with its own logo only, a switch and its findings", async () => {
    const fetchMock = stub();
    const zap = await screen.findByTestId("scorecard-tool-zap");
    expect(within(zap).getByRole("img", { name: "OWASP ZAP logo" })).toBeInTheDocument();
    const bandit = screen.getByTestId("scorecard-tool-bandit");
    expect(within(bandit).queryByRole("img", { name: /logo/ })).not.toBeInTheDocument();
    expect(within(bandit).getByText("Bandit said 50")).toBeInTheDocument();

    fireEvent.click(within(bandit).getByRole("button", { expanded: false }));
    expect(within(bandit).getByText("B602")).toBeInTheDocument();

    const toggle = within(bandit).getByRole("switch", { name: "Use Bandit" });
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "false"));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(String(put?.[0])).toContain("/api/v1/scorecard/tools/bandit");
    // With Bandit off, only Ruff counts.
    await waitFor(() => expect(screen.getByRole("img", { name: "Release score: 100 out of 100, grade A" })).toBeInTheDocument());
  });

  it("scans a repository and shows each tool's progress", async () => {
    const fetchMock = stub({ status: "empty", categories: [], tools: [], versions: [] });
    expect(await screen.findByText("Nothing scored yet")).toBeInTheDocument();
    const form = screen.getByTestId("scorecard-scan");
    fireEvent.change(within(form).getByLabelText("Repository"), { target: { value: "https://github.com/torvalds/linux" } });
    fireEvent.click(within(form).getByRole("button", { name: "Scan" }));
    expect(await within(form).findByText("Running 1 tool(s)")).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ target: "https://github.com/torvalds/linux", url: null });
    expect(within(form).getByText(/Not run \(1\)/)).toBeInTheDocument();
  });
});
