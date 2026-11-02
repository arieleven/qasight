import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ResourcesPage } from "./ResourcesPage";

// A snapshot taken long ago: times on the page count from it, not from today.
const SNAPSHOT_AT = 1_700_000_000;

vi.mock("../../lib/static-backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/static-backend")>()),
  IS_STATIC_BUILD: true,
  loadManifest: async () => ({ mode: "static", generated_at: SNAPSHOT_AT, run_ids: [], latest_run_id: null })
}));

const demo = {
  pools: [
    {
      name: "demo-flow-vms",
      free: 1,
      waiting: 0,
      items: [
        {
          item: "demo-vm-01",
          labels: ["win11"],
          data: { address: "10.0.0.11" },
          disabled: false,
          disabled_reason: "",
          disabled_by: null,
          lease: {
            holder: { user: "alice", host: "alice-laptop", pid: 1, run_id: "", stage: "flows/checkout", worker: "gw0", note: "" },
            taken_at: SNAPSHOT_AT - 420,
            heartbeat_at: SNAPSHOT_AT - 10,
            expires_at: SNAPSHOT_AT + 3600,
            ttl_s: 3600
          }
        },
        { item: "demo-vm-02", labels: [], data: {}, disabled: false, disabled_reason: "", disabled_by: null, lease: null }
      ]
    }
  ]
};

describe("ResourcesPage in the read-only demo", () => {
  it("shows the made-up pools as of the snapshot, without buttons", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, status: 200, json: async () => demo })));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ResourcesPage />
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText("demo-flow-vms")).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByTestId("resource-key")[0]).toHaveTextContent("taken 7m ago · last heartbeat 10s ago"));
    expect(screen.getByTestId("resources-snapshot-note")).toHaveTextContent("made up");
    expect(screen.getByTestId("resources-snapshot-note")).toHaveTextContent(new Date(SNAPSHOT_AT * 1000).toUTCString());
    // Only the cards' Copy menus: no lease or pool actions in the demo.
    const actions = screen.queryAllByRole("button").filter((b) => !b.getAttribute("aria-label")?.startsWith("Copy or export"));
    expect(actions).toHaveLength(0);
  });
});
