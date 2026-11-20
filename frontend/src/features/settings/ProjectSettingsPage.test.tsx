import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ProjectSettingsPage } from "./ProjectSettingsPage";

describe("ProjectSettingsPage", () => {
  it("shows the project folder, retention and each setting with where it came from", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/v1/run-settings")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              config_found: true,
              project_folder: "/work/shop",
              retention: { days: 30, runs_per_cycle: null, enabled: true },
              cleanup_interval_hours: 6,
              default_stage_timeout_s: 600,
              settings: [
                { name: "quarantine.auto", env: "TESTO_QUARANTINE_AUTO", value: "0.3", source: "qasight.yaml" },
                { name: "publish", env: "TESTO_PUBLISH", value: null, source: "default" }
              ]
            })
          });
        }
        return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
      })
    );
    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <ProjectSettingsPage />
        </QueryClientProvider>
      </MemoryRouter>
    );

    await waitFor(() => expect(screen.getByTestId("project-folder")).toHaveTextContent("/work/shop"));
    await waitFor(() => expect(screen.getByTestId("run-settings")).toHaveTextContent("Runs older than 30 days are deleted automatically every 6 hours."));
    const list = screen.getByTestId("project-settings");
    expect(list).toHaveTextContent("quarantine.auto");
    expect(list).toHaveTextContent("from qasight.yaml");
    expect(screen.getByRole("link", { name: "Project" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Resources" })).toHaveAttribute("href", "/resources");
  });
});
