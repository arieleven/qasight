import { describe, expect, it } from "vitest";

import { escapeHtml, slugify, tableMatrix, textToHtml, toCsv, toHtmlTable, toTsv } from "./clipboard";

function table(html: string): HTMLElement {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div;
}

describe("clipboard helpers", () => {
  it("reads a table as rows of cell text", () => {
    const root = table(
      "<table><thead><tr><th>Test</th><th>Status</th></tr></thead><tbody><tr><td> login\n works </td><td>failed</td></tr></tbody></table>"
    );
    expect(tableMatrix(root)).toEqual([
      ["Test", "Status"],
      ["login works", "failed"]
    ]);
    expect(tableMatrix(table("<p>no table</p>"))).toBeNull();
  });

  it("writes CSV that quotes only what needs it", () => {
    expect(toCsv([["a", "b,c"], ['say "hi"', "x"]])).toBe('a,"b,c"\r\n"say ""hi""",x\r\n');
  });

  it("writes tab-separated text for spreadsheets", () => {
    expect(toTsv([["a\tb", "c"], ["d", "e\nf"]])).toBe("a b\tc\nd\te f");
  });

  it("escapes cell text in the HTML table", () => {
    const html = toHtmlTable([["Name"], ["<script>"]], "Flaky & slow");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("Flaky &amp; slow");
    expect(html).not.toContain("<script>");
    expect(escapeHtml('"')).toBe("&quot;");
  });

  it("turns lines into HTML blocks", () => {
    expect(textToHtml("one\n\n two ")).toBe("<div>one</div><div>two</div>");
  });

  it("makes anchors and file names from titles", () => {
    expect(slugify("Failing tests (3)")).toBe("failing-tests-3");
    expect(slugify("!!!")).toBe("card");
  });
});
