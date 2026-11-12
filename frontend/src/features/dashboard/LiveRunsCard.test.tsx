import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LiveRunItem } from "../../lib/api-client";
import { LiveRunRow } from "./LiveRunsCard";

const item: LiveRunItem = {
  id: "abc",
  cycle: "shop-unit",
  started_at: 0,
  elapsed_s: 42,
  total: 200,
  done: 50,
  passed: 46,
  failed: 3,
  skipped: 0,
  errors: 1,
  branch: "main",
  commit: null,
  current: "tests/test_cart.py::test_total",
  finished: false,
  failures: [
    { test: "tests/test_cart.py::test_discount", message: "def test_discount():\nE   assert 90 == 100" },
    { test: "tests/test_cart.py::test_tax", message: "" },
    { test: "tests/test_pay.py::test_card", message: "" },
    { test: "tests/test_pay.py::test_refund", message: "" }
  ]
};

describe("LiveRunRow", () => {
  it("shows progress, counts, the last test and the first failures", () => {
    render(<LiveRunRow item={item} />);
    expect(screen.getByRole("progressbar", { name: "shop-unit progress" })).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByText(/50 of 200 tests/)).toBeInTheDocument();
    expect(screen.getByText("4 failed", { exact: false })).toHaveClass("text-danger-300");
    expect(screen.getByText("Last: tests/test_cart.py::test_total")).toBeInTheDocument();
    expect(screen.getByText("E assert 90 == 100")).toBeInTheDocument();
    expect(screen.getByText("and 1 more")).toBeInTheDocument();
    expect(screen.getByText("42.0 s so far")).toBeInTheDocument();
  });

  it("says when the session ended and the total is unknown", () => {
    render(<LiveRunRow item={{ ...item, total: null, finished: true, failures: [], failed: 0, errors: 0 }} />);
    expect(screen.getByText("finished, uploading the report")).toBeInTheDocument();
    expect(screen.getByText(/^50 tests/)).toBeInTheDocument();
    expect(screen.queryByText(/Last:/)).not.toBeInTheDocument();
  });
});
