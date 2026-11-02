import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EnvironmentsCard } from "./TestPage";

describe("EnvironmentsCard", () => {
  it("says which environment the test fails on", () => {
    render(
      <EnvironmentsCard
        environments={[
          { environment: "browser=firefox", runs: 3, passed: 0, failed: 3, last_status: "failed" },
          { environment: "browser=chromium", runs: 3, passed: 3, failed: 0, last_status: "passed" }
        ]}
      />
    );
    expect(screen.getByText("Fails only on browser=firefox.")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Results by environment" }).children).toHaveLength(2);
  });

  it("renders nothing when no run recorded an environment", () => {
    const { container } = render(<EnvironmentsCard environments={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
