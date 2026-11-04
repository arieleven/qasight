import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TryPage } from "./TryPage";

function upload(content: string, name = "report.xml") {
  const file = new File([content], name, { type: "text/xml" });
  fireEvent.change(screen.getByLabelText("JUnit XML files"), { target: { files: [file] } });
}

describe("TryPage", () => {
  it("shows the sample report: verdict, failing tests and the shared cause", () => {
    render(<TryPage />);

    fireEvent.click(screen.getByRole("button", { name: "Use a sample report" }));

    expect(screen.getByText(/3 tests of 6 failed/)).toBeInTheDocument();
    const failing = screen.getByRole("heading", { name: "Failing tests (3)" }).closest("section, div") as HTMLElement;
    expect(within(failing.parentElement ?? failing).getByText("Same cause")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Slowest tests" })).toBeInTheDocument();
    expect(screen.getByText(/nothing was uploaded/)).toBeInTheDocument();
    expect(screen.getByTestId("try-setup")).toHaveTextContent("qasight init");
  });

  it("reads a chosen file in the browser", async () => {
    render(<TryPage />);

    upload(`<testsuite name="s"><testcase name="test_ok" time="0.2"/></testsuite>`, "ok.xml");

    expect(await screen.findByText(/All 1 test passed/)).toBeInTheDocument();
    expect(screen.getByText(/From ok.xml\./)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Failing tests/ })).not.toBeInTheDocument();
  });

  it("names the file that couldn't be read", async () => {
    render(<TryPage />);

    upload("<html/>", "page.html");

    expect(await screen.findByRole("alert")).toHaveTextContent(/^page\.html: Expected a JUnit/);
  });
});
