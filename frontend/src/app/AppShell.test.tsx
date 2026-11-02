import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

function renderShell() {
  const page = (name: string) => <h1>{name} page</h1>;
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <AppShell />,
        children: [
          { index: true, element: page("Dashboard") },
          { path: "cycles", element: page("Cycles") },
          { path: "quick-run", element: page("Quick Run") }
        ]
      }
    ],
    { initialEntries: ["/"] }
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

describe("AppShell mobile menu", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 404, json: async () => ({}) })));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("toggles a disclosure panel with every nav link", () => {
    renderShell();
    const toggle = screen.getByRole("button", { name: "Menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByRole("navigation", { name: "Main" });
    expect(toggle).toHaveAttribute("aria-controls", panel.id);
    const labels = within(panel)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(labels).toEqual(["Dashboard", "Cycles", "Runs", "Compare", "Project health", "Report", "Ask", "Quick run", "Resources", "Try a file", "AI settings"]);

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("closes after navigating", async () => {
    renderShell();
    const toggle = screen.getByRole("button", { name: "Menu" });
    fireEvent.click(toggle);
    const panel = screen.getByRole("navigation", { name: "Main" });

    fireEvent.click(within(panel).getByRole("link", { name: "Quick run" }));

    expect(await screen.findByRole("heading", { name: "Quick Run page" })).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the toggle", () => {
    renderShell();
    const toggle = screen.getByRole("button", { name: "Menu" });
    fireEvent.click(toggle);
    within(screen.getByRole("navigation", { name: "Main" }))
      .getByRole("link", { name: "Cycles" })
      .focus();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("closes on a click outside the panel", () => {
    renderShell();
    const toggle = screen.getByRole("button", { name: "Menu" });
    fireEvent.click(toggle);

    fireEvent.mouseDown(screen.getByRole("heading", { name: "Dashboard page" }));

    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });
});

describe("AppShell user badge", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stubMe(body: object) {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve(
          url.endsWith("/api/v1/auth/me")
            ? { ok: true, status: 200, json: async () => body }
            : { ok: false, status: 404, json: async () => ({}) }
        )
      )
    );
  }

  it("shows who is signed in when the server has accounts", async () => {
    stubMe({ auth_mode: "accounts", signed_in: true, username: "ada", role: "admin" });
    renderShell();
    const badges = await screen.findAllByTestId("user-badge");
    expect(badges[0]).toHaveTextContent("ada · admin");
    expect(within(badges[0]).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("stays hidden with the shared token", async () => {
    stubMe({ auth_mode: "token", signed_in: true, username: "token", role: "admin" });
    renderShell();
    await screen.findAllByTestId("health-indicator");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByTestId("user-badge")).not.toBeInTheDocument();
  });
});

describe("AppShell eye logo", () => {
  function renderAt(path: string, score: number) {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        String(url).includes("/api/v1/project-health")
          ? Promise.resolve({ ok: true, status: 200, json: async () => ({ score, cycles: 1 }) })
          : Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
      )
    );
    const router = createMemoryRouter(
      [{ path: "/", element: <AppShell />, children: [{ index: true, element: <h1>Dashboard page</h1> }, { path: "health", element: <h1>Health page</h1> }] }],
      { initialEntries: [path] }
    );
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    );
  }
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("tints the iris with the health score colour on the health page", async () => {
    renderAt("/health", 50);
    await waitFor(() => expect(screen.getAllByTestId("eye-logo-iris")[0]).toHaveAttribute("fill", "#f87171"));
  });

  it("stays brand blue on other pages", async () => {
    renderAt("/", 50);
    await waitFor(() => expect(screen.getAllByTestId("eye-logo")[0]).toHaveAttribute("data-spinning", "false"));
    expect(screen.getAllByTestId("eye-logo-iris")[0]).toHaveAttribute("fill", "#0075ff");
  });
});
