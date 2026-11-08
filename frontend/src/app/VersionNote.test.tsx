import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VersionNote } from "./AppShell";

function renderWith(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, status: 200, json: async () => body }))
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <VersionNote />
    </QueryClientProvider>
  );
}

describe("VersionNote", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("links to the release when a newer one is out", async () => {
    renderWith({ version: "1.5.0", latest: "1.6.0", update_available: true });
    expect(await screen.findByText(/qasight 1\.5\.0/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1.6.0 is out" })).toHaveAttribute(
      "href",
      "https://github.com/arieleven/qasight/releases"
    );
  });

  it("shows no update link when the version is the newest or unknown", async () => {
    renderWith({ version: "1.6.0", latest: null, update_available: false });
    expect(await screen.findByTestId("version-note")).toHaveTextContent("qasight 1.6.0 · made by arieleven");
    expect(screen.queryByRole("link", { name: /is out/ })).not.toBeInTheDocument();
  });

  it("credits the author with a link to their GitHub", async () => {
    renderWith({ version: "1.6.0", latest: null, update_available: false });
    expect(await screen.findByRole("link", { name: "arieleven" })).toHaveAttribute("href", "https://github.com/arieleven");
  });
});
