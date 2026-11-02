import { describe, expect, it } from "vitest";

import { retentionLine, timeoutLine } from "./RunSettingsNote";

const base = { config_found: true, cleanup_interval_hours: 6, default_stage_timeout_s: 600 };

describe("run settings lines", () => {
  it("names both retention limits and how often they apply", () => {
    const line = retentionLine({ ...base, retention: { days: 30, runs_per_cycle: 50, enabled: true } });
    expect(line).toBe("Runs older than 30 days or beyond the newest 50 of a cycle are deleted automatically every 6 hours.");
  });

  it("says runs are kept when there is no retention", () => {
    const line = retentionLine({ ...base, cleanup_interval_hours: null, retention: { days: null, runs_per_cycle: null, enabled: false } });
    expect(line).toContain("kept until you delete them");
  });

  it("describes the stage time limit, or its absence", () => {
    const retention = { days: null, runs_per_cycle: null, enabled: false };
    expect(timeoutLine({ ...base, retention })).toBe("A stage is stopped after 600 s unless it sets its own timeout_s.");
    expect(timeoutLine({ ...base, default_stage_timeout_s: null, retention })).toContain("no time limit");
    expect(timeoutLine({ ...base, config_found: false, retention })).toBeNull();
  });
});
