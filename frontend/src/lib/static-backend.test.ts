import { afterEach, describe, expect, it, vi } from "vitest";

import { resolveStaticPath, stableId } from "./static-backend";

function resolve(url: string): string | null {
  const parsed = new URL(url, "https://example.gitlab.io/qasight/");
  return resolveStaticPath(parsed.pathname, parsed.searchParams);
}

describe("resolveStaticPath", () => {
  it("maps the collection endpoints the pages load", () => {
    expect(resolve("/api/v1/runs")).toBe("runs.json");
    expect(resolve("/api/v1/runs?limit=30")).toBe("runs.json");
    expect(resolve("/api/v1/cycles")).toBe("cycles.json");
    expect(resolve("/api/v1/health/ready")).toBe("health.json");
    expect(resolve("/api/v1/ai/config/status")).toBe("ai-config.json");
    expect(resolve("/api/v1/dashboard/overview?recent_limit=6")).toBe("dashboard/overview.json");
    expect(resolve("/api/v1/dashboard/runs/recent?limit=8")).toBe("dashboard/recent-runs.json");
    expect(resolve("/api/v1/dashboard/attention")).toBe("dashboard/attention.json");
    expect(resolve("/api/v1/dashboard/delivery?days=7")).toBe("dashboard/delivery.json");
    expect(resolve("/api/v1/project-health")).toBe("project-health/all.json");
    expect(resolve("/api/v1/project-health?cycle=web")).toBe("project-health/cycles/web.json");
    expect(resolve("/api/v1/project-health/skills")).toBe("project-health/skills.json");
    expect(resolve("/api/v1/project-health/ai-review?cycle=web")).toBe("project-health/ai-review/web.json");
  });

  it("maps per-run endpoints", () => {
    expect(resolve("/api/v1/runs/abc-123")).toBe("runs/abc-123/detail.json");
    expect(resolve("/api/v1/runs/abc-123/reports")).toBe("runs/abc-123/reports.json");
    expect(resolve("/api/v1/runs/abc-123/pyramid")).toBe("runs/abc-123/pyramid.json");
    expect(resolve("/api/v1/runs/abc-123/ai-summary")).toBe("runs/abc-123/ai-summary.json");
    expect(resolve("/api/v1/cycles/sample-pytests")).toBe("cycles/sample-pytests.json");
  });

  it("turns delta query parameters into the exported pair folder", () => {
    expect(resolve("/api/v1/analytics/delta?current_run_id=aaa&baseline_run_id=bbb")).toBe(
      "delta/aaa__bbb/comparison.json"
    );
    expect(resolve("/api/v1/analytics/delta/cases?current_run_id=aaa&baseline_run_id=bbb")).toBe(
      "delta/aaa__bbb/cases.json"
    );
  });

  it("maps flaky tests to the cycle's exported file", () => {
    expect(resolve("/api/v1/analytics/flaky?cycle=fake-api")).toBe("flaky/fake-api.json");
    expect(resolve("/api/v1/analytics/flaky")).toBeNull();
  });

  it("works when the API base URL puts the site subpath in front of /api/v1", () => {
    expect(resolve("https://example.gitlab.io/qasight/api/v1/runs")).toBe("runs.json");
  });

  it("has nothing to serve for streams, writes and incomplete comparisons", () => {
    expect(resolve("/api/v1/cycle-executions/xyz/events")).toBeNull();
    expect(resolve("/api/v1/cycles/sample-pytests/executions")).toBeNull();
    expect(resolve("/api/v1/analytics/delta?current_run_id=aaa")).toBeNull();
  });
});

describe("installStaticBackend", () => {
  async function installWith(files: Record<string, unknown>) {
    vi.resetModules();
    vi.stubEnv("VITE_STATIC_DATA_BASE", "/demo/data");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = String(input);
        return Promise.resolve(
          path in files
            ? new Response(JSON.stringify(files[path]), { status: 200 })
            : new Response("missing", { status: 404 })
        );
      })
    );
    const backend = await import("./static-backend");
    backend.installStaticBackend();
    return backend;
  }

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("serves GETs from the exported files", async () => {
    const summary = { status: "available", summary_text: "The /broken route returns 500." };
    await installWith({ "/demo/data/runs/run-1/ai-summary.json": summary });

    const resp = await window.fetch("/api/v1/runs/run-1/ai-summary");

    expect(resp.status).toBe(200);
    expect(await resp.json()).toEqual(summary);
  });

  it("refuses writes with a host-neutral read-only message", async () => {
    const backend = await installWith({});

    const resp = await window.fetch("/api/v1/cycles/self-test/executions", { method: "POST" });

    expect(resp.status).toBe(405);
    const body = await resp.json();
    expect(body.error.code).toBe("read_only_build");
    expect(body.error.message).toBe(backend.READ_ONLY_MESSAGE);
    expect(backend.READ_ONLY_MESSAGE).not.toMatch(/GitLab|GitHub/);
  });

  it("maps the per-test endpoints onto hashed file names", () => {
    expect(stableId("")).toBe("811c9dc5");
    expect(stableId("a")).toBe("e40c292c");
    expect(resolve("/api/v1/analytics/tests?cycle=smoke")).toBe("tests/smoke.json");
    expect(resolve("/api/v1/analytics/test-history?cycle=smoke&key=a")).toBe("test-history/smoke/e40c292c.json");
    expect(resolve("/api/v1/analytics/test-history?cycle=smoke")).toBeNull();
  });
});

describe("runtime static mode (qasight export-site)", () => {
  afterEach(() => {
    document.head.querySelectorAll('meta[name="testo-static-data"], base').forEach((el) => el.remove());
    vi.resetModules();
  });

  it("stays live without the meta tag", async () => {
    vi.resetModules();
    const backend = await import("./static-backend");
    expect(backend.IS_STATIC_BUILD).toBe(false);
  });

  it("switches on from the meta tag, resolved against the page's base, as a snapshot not the demo", async () => {
    const base = document.createElement("base");
    base.href = "/my-repo/";
    const meta = document.createElement("meta");
    meta.name = "testo-static-data";
    meta.content = "data/";
    document.head.append(base, meta);
    vi.resetModules();
    const backend = await import("./static-backend");
    expect(backend.IS_STATIC_BUILD).toBe(true);
    expect(backend.IS_DEMO_BUILD).toBe(false);
    expect(backend.STATIC_LABEL).toBe("Read-only snapshot");

    const realFetch = vi.fn(async (url: string) => new Response(JSON.stringify({ url }), { status: 200 }));
    vi.stubGlobal("fetch", realFetch);
    backend.installStaticBackend();
    const body = await (await window.fetch("/api/v1/runs")).json();
    expect(body.url).toBe("/my-repo/data/runs.json");
    vi.unstubAllGlobals();
  });
});
