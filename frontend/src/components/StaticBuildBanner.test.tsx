import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/static-backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/static-backend")>()),
  IS_STATIC_BUILD: true,
  IS_DEMO_BUILD: true,
  STATIC_LABEL: "Read-only demo",
  loadManifest: async () => null
}));

// The dismissed flag is read once at module load, so each test imports a fresh copy.
async function renderNotice() {
  vi.resetModules();
  const { StaticBuildBanner, DemoBadge } = await import("./StaticBuildBanner");
  render(
    <>
      <DemoBadge />
      <StaticBuildBanner />
    </>
  );
}

describe("read-only demo notice", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("closes with the × button and stays closed on reload", async () => {
    await renderNotice();
    expect(screen.getByRole("region", { name: "Read-only demo" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dismiss read-only demo notice" }));
    expect(screen.queryByRole("region", { name: "Read-only demo" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("testo.demoNotice.dismissed")).toBe("1");

    document.body.innerHTML = "";
    await renderNotice();
    expect(screen.queryByRole("region", { name: "Read-only demo" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read-only demo" })).toHaveAttribute("aria-expanded", "false");
  });

  it("reopens from the header badge", async () => {
    window.localStorage.setItem("testo.demoNotice.dismissed", "1");
    await renderNotice();

    fireEvent.click(screen.getByRole("button", { name: "Read-only demo" }));
    expect(screen.getByRole("region", { name: "Read-only demo" })).toBeInTheDocument();
    expect(window.localStorage.getItem("testo.demoNotice.dismissed")).toBeNull();
  });
});
