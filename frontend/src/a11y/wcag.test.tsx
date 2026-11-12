import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import axe from "axe-core";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { routes } from "../app/routes";

// WCAG 2.1 A and AA rules on every page of the app, as it renders before data
// arrives or with an empty server. Colour contrast needs real layout, which jsdom
// doesn't have: Lighthouse checks it on the demo site in the release gate.
const PAGES = [
  "/",
  "/cycles",
  "/runs",
  "/compare",
  "/releases",
  "/test-health",
  "/code-quality",
  "/report",
  "/ask",
  "/resources",
  "/quick-run",
  "/try",
  "/settings/ai",
  "/no-such-page"
];

async function violations(container: HTMLElement): Promise<string[]> {
  const result = await axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    rules: { "color-contrast": { enabled: false } }
  });
  return result.violations.map(
    (v) => `${v.id}: ${v.help} (${v.nodes.length}) ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`
  );
}

describe("WCAG 2.1 AA (axe)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(PAGES)("%s has no violations", async (path) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 404, json: async () => ({}) }))
    );
    const router = createMemoryRouter(routes, { initialEntries: [path] });
    const { container } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    );
    await screen.findAllByRole("navigation");
    expect(await violations(container)).toEqual([]);
  });
});
