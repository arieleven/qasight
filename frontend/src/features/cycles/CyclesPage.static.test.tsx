import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { CyclesPage } from "./CyclesPage";

vi.mock("../../lib/static-backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/static-backend")>()),
  IS_STATIC_BUILD: true
}));

describe("CyclesPage in the read-only demo", () => {
  it("disables Run and shows the local command instead", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          items: [{ name: "fake-api", description: "Demo app.", stage_count: 4, equipment: ["pytest"], source: "config" }],
          config_path: "qasight.yaml"
        })
      })
    );

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <CyclesPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByRole("link", { name: "fake-api" })).toBeInTheDocument());
    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    expect(run.parentElement).toHaveAttribute("title", expect.stringContaining("qasight run --cycle fake-api"));
    expect(screen.getByText("qasight run --cycle fake-api")).toBeInTheDocument();
  });
});
