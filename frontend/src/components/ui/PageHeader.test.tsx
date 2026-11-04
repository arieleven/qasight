import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("names the browser tab after the page", () => {
    render(<PageHeader title="Runs" />);
    expect(document.title).toBe("Runs · QASight");
  });

  it("takes a plain-text tab title when the heading is markup", () => {
    render(<PageHeader title={<span>web · Oct 31</span>} documentTitle="web · Oct 31" />);
    expect(document.title).toBe("web · Oct 31 · QASight");
  });
});
