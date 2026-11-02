import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Delivery } from "../../lib/api-client";
import { DeliveryStats } from "./DeliveryCard";

const delivery: Delivery = {
  default_branch: "main",
  fixes: 4,
  median_fix_s: 5 * 3600,
  prev_median_fix_s: 2 * 3600,
  open_failures: 3,
  open_by_age: [
    { label: "Under a day", count: 1 },
    { label: "1–7 days", count: 0 },
    { label: "1–4 weeks", count: 1 },
    { label: "Over a month", count: 1 }
  ],
  oldest_open: [],
  ci_minutes: 42.4,
  prev_ci_minutes: 50,
  red_pct: 12.5,
  prev_red_pct: null,
  longest_red_s: 7200,
  first_try_green_pct: null,
  prev_first_try_green_pct: null,
  first_try_commits: 0,
  cycles: []
};

describe("DeliveryStats", () => {
  it("shows time to fix, open failures, red time and CI time, with lower-is-better colours", () => {
    render(<DeliveryStats delivery={delivery} />);
    expect(screen.getByTestId("delivery-fix")).toHaveTextContent("5hmedian of 4 fixes · was 2h");
    expect(screen.getByText("· was 2h")).toHaveClass("text-danger-400");
    expect(screen.getByTestId("delivery-open")).toHaveTextContent("32 older than a week");
    expect(screen.getByTestId("delivery-red")).toHaveTextContent("main red13%longest spell 2h");
    expect(screen.getByTestId("delivery-ci")).toHaveTextContent("42 min");
    expect(screen.getByText("· was 50 min")).toHaveClass("text-success-400");
  });

  it("says so when nothing was fixed and no branch was recorded", () => {
    render(
      <DeliveryStats
        delivery={{ ...delivery, default_branch: null, fixes: 0, median_fix_s: null, red_pct: null, open_failures: 0 }}
      />
    );
    expect(screen.getByText("No failing test was fixed")).toBeInTheDocument();
    expect(screen.getByText("Default branch red")).toBeInTheDocument();
    expect(screen.getByText("Nothing failing")).toBeInTheDocument();
  });
});
