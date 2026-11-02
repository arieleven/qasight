import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Card } from "./Card";

afterEach(() => vi.unstubAllGlobals());

describe("Card copy menu", () => {
  it("offers text, table, CSV and link for a card with a table", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(
      <Card title="Slowest tests">
        <table>
          <tbody>
            <tr>
              <th>Test</th>
              <th>Time</th>
            </tr>
            <tr>
              <td>checkout</td>
              <td>4.2 s</td>
            </tr>
          </tbody>
        </table>
      </Card>
    );

    expect(document.getElementById("card-slowest-tests")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy or export Slowest tests" }));
    const items = screen.getAllByRole("menuitem").map((b) => b.textContent);
    expect(items).toEqual(["Copy as text", "Copy as table", "Download CSV", "Copy link to this card"]);

    // No ClipboardItem in jsdom: the table falls back to tab-separated text.
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy as table" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Test\tTime\ncheckout\t4.2 s"));
    expect(await screen.findByText("Table copied")).toBeInTheDocument();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("can be turned off", () => {
    render(<Card title="Quiet" copy={false} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
