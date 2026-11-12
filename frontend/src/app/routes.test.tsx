import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { routes } from "./routes";

describe("routes", () => {
  it.each(["/history", "/runner", "/execution", "/no-such-page"])("shows the not-found page for %s", async (path) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 404, json: async () => ({}) }))
    );
    const router = createMemoryRouter(routes, { initialEntries: [path] });

    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    );

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(path);
    expect(screen.getByRole("link", { name: "Go to overview" })).toHaveAttribute("href", "/");
  });

  it.each([
    ["/health?cycle=api", "/test-health", "?cycle=api"],
    ["/scorecard?version=1.6.0", "/code-quality", "?version=1.6.0"]
  ])("moves the renamed page %s to its new address", async (path, pathname, search) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: false, status: 404, json: async () => ({}) }))
    );
    const router = createMemoryRouter(routes, { initialEntries: [path] });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    );
    await waitFor(() => expect(router.state.location.pathname).toBe(pathname));
    expect(router.state.location.search).toBe(search);
  });
});
