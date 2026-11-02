import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { StorageNote, formatBytes } from "./StorageNote";

const GB = 1024 ** 3;

function renderWith(body: object) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve({ ok: true, json: async () => body }))
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <StorageNote />
    </QueryClientProvider>
  );
}

const base = {
  runs: 1234,
  report_archives: 0,
  report_archive_bytes: 0,
  history_files_bytes: 3 * GB,
  database_bytes: 512 * 1024 * 1024,
  database_dialect: "postgresql",
  measured_at: 0
};

describe("StorageNote", () => {
  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(null)).toBe("unknown");
  });

  it("shows how much the history takes", async () => {
    renderWith({ ...base, disk: { path: "/data", total_bytes: 100 * GB, free_bytes: 60 * GB, free_pct: 60, low: false } });
    const note = await screen.findByTestId("storage-note");
    expect(note).toHaveTextContent("1,234 runs");
    expect(note).toHaveTextContent("database 512.0 MB");
    expect(note).toHaveTextContent("files 3.0 GB");
    expect(note).toHaveTextContent("60.0 GB free on disk (60%)");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("warns when the disk is low", async () => {
    renderWith({ ...base, disk: { path: "/data", total_bytes: 100 * GB, free_bytes: 5 * GB, free_pct: 5, low: true } });
    expect(await screen.findByRole("status")).toHaveTextContent("The disk is getting full");
  });
});
