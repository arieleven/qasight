/**
 * Getting results out of the UI: copy a card as text or as a table, a chart as
 * an image, or a table as a CSV file, so it pastes cleanly into an email, Slack,
 * Confluence, Notion, Google Docs or a slide.
 *
 * Rich copies put `text/html` and `text/plain` on the clipboard together: rich
 * editors keep the table, plain fields get tab-separated text. The async
 * Clipboard API needs a secure context (HTTPS or localhost); without it the
 * plain-text fallback is used, and when even that is refused the caller shows
 * a message instead of pretending it worked.
 */

export type Matrix = string[][];

/** Collapse whitespace the way a reader sees the cell. */
function cellText(el: Element): string {
  return (el.textContent ?? "").replace(/\s+/g, " ").trim();
}

/** The first `<table>` inside `root` as rows of cell text (header row first), or null. */
export function tableMatrix(root: Element): Matrix | null {
  const table = root.querySelector("table");
  if (!table) return null;
  const rows = Array.from(table.querySelectorAll("tr"))
    .map((tr) => Array.from(tr.querySelectorAll("th, td")).map(cellText))
    .filter((row) => row.length > 0);
  return rows.length ? rows : null;
}

/** Tab-separated text: pastes into a spreadsheet as cells. */
export function toTsv(rows: Matrix): string {
  return rows.map((row) => row.map((c) => c.replace(/[\t\n]/g, " ")).join("\t")).join("\n");
}

/** RFC 4180 CSV, quoting only cells that need it. */
export function toCsv(rows: Matrix): string {
  const quote = (c: string) => (/[",\n\r]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c);
  return rows.map((row) => row.map(quote).join(",")).join("\r\n") + "\r\n";
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** A plain, inline-styled HTML table: survives pasting into email and wiki editors. */
export function toHtmlTable(rows: Matrix, title?: string): string {
  const cell = "border:1px solid #d0d5dd;padding:4px 8px;text-align:left;vertical-align:top";
  const [head, ...body] = rows;
  const th = head.map((c) => `<th style="${cell};background:#f2f4f7">${escapeHtml(c)}</th>`).join("");
  const tr = body
    .map((row) => `<tr>${row.map((c) => `<td style="${cell}">${escapeHtml(c)}</td>`).join("")}</tr>`)
    .join("");
  const caption = title ? `<p><strong>${escapeHtml(title)}</strong></p>` : "";
  return `${caption}<table style="border-collapse:collapse;font-family:sans-serif;font-size:13px"><thead><tr>${th}</tr></thead><tbody>${tr}</tbody></table>`;
}

/** Lines of text as HTML paragraphs, the first one bold when it is a title. */
export function textToHtml(text: string, title?: string): string {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const heading = title ? `<p><strong>${escapeHtml(title)}</strong></p>` : "";
  return heading + lines.map((l) => `<div>${escapeHtml(l)}</div>`).join("");
}

/**
 * The text a reader sees in `root`, with elements marked `data-copy-exclude`
 * (the copy menu itself, buttons that only make sense on screen) left out.
 * `innerText` respects `display: none`, so those are hidden for the read.
 */
export function visibleText(root: HTMLElement): string {
  root.setAttribute("data-copying", "");
  try {
    const text = typeof root.innerText === "string" ? root.innerText : (root.textContent ?? "");
    return text.replace(/\n{3,}/g, "\n\n").trim();
  } finally {
    root.removeAttribute("data-copying");
  }
}

async function writeItem(parts: Record<string, Blob>, fallbackText?: string): Promise<boolean> {
  const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (clip && typeof ClipboardItem !== "undefined" && typeof clip.write === "function") {
    try {
      await clip.write([new ClipboardItem(parts)]);
      return true;
    } catch {
      // Denied or unsupported type: fall through to plain text.
    }
  }
  if (fallbackText !== undefined && clip && typeof clip.writeText === "function") {
    try {
      await clip.writeText(fallbackText);
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

/** Put `html` and `text` on the clipboard together; false when the browser refused. */
export function copyRich(html: string, text: string): Promise<boolean> {
  return writeItem(
    {
      "text/html": new Blob([html], { type: "text/html" }),
      "text/plain": new Blob([text], { type: "text/plain" })
    },
    text
  );
}

export async function copyText(text: string): Promise<boolean> {
  const clip = typeof navigator !== "undefined" ? navigator.clipboard : undefined;
  if (!clip || typeof clip.writeText !== "function") return false;
  try {
    await clip.writeText(text);
    return true;
  } catch {
    return false;
  }
}

const STYLE_PROPS = [
  "fill",
  "fill-opacity",
  "stroke",
  "stroke-width",
  "stroke-opacity",
  "stroke-dasharray",
  "opacity",
  "font-family",
  "font-size",
  "font-weight",
  "text-anchor",
  "dominant-baseline"
];

/**
 * A standalone copy of a chart: Tailwind classes don't travel with an SVG, so
 * every element gets its computed paint inlined, and the root gets a size and
 * a background so it reads on a white slide or a dark chat.
 */
export function standaloneSvg(svg: SVGSVGElement, background = "#ffffff"): { markup: string; width: number; height: number } {
  const box = svg.getBoundingClientRect();
  const width = Math.max(1, Math.round(box.width || Number(svg.getAttribute("width")) || 600));
  const height = Math.max(1, Math.round(box.height || Number(svg.getAttribute("height")) || 300));
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const source = [svg, ...Array.from(svg.querySelectorAll("*"))];
  const target = [clone, ...Array.from(clone.querySelectorAll("*"))];
  source.forEach((el, i) => {
    const computed = window.getComputedStyle(el);
    const style = STYLE_PROPS.map((p) => {
      const v = computed.getPropertyValue(p);
      return v ? `${p}:${v}` : "";
    })
      .filter(Boolean)
      .join(";");
    if (style) (target[i] as Element).setAttribute("style", style);
  });
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  clone.removeAttribute("class");
  const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bg.setAttribute("width", "100%");
  bg.setAttribute("height", "100%");
  bg.setAttribute("fill", background);
  clone.insertBefore(bg, clone.firstChild);
  return { markup: new XMLSerializer().serializeToString(clone), width, height };
}

/** Render a chart to a PNG at `scale`x, for crisp pasting into slides. */
export async function svgToPng(svg: SVGSVGElement, scale = 2, background = "#ffffff"): Promise<Blob> {
  const { markup, width, height } = standaloneSvg(svg, background);
  const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const img = new Image();
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("could not draw the chart"));
      img.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = width * scale;
    canvas.height = height * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no canvas");
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, width, height);
    return await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("could not encode the image"))), "image/png")
    );
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function copyImage(png: Blob): Promise<boolean> {
  return writeItem({ "image/png": png });
}

/** Save `data` as a file through a temporary link. */
export function downloadBlob(filename: string, data: Blob): void {
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** `"Failing tests (3)"` → `"failing-tests-3"`, for anchors and file names. */
export function slugify(text: string): string {
  return (
    text
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^\w\s-]/g, "")
      .trim()
      .replace(/[\s_]+/g, "-")
      .replace(/-+/g, "-")
      .slice(0, 60) || "card"
  );
}

/** The largest chart-sized SVG in `root` (icons are skipped), or null. */
export function findChart(root: Element): SVGSVGElement | null {
  let best: SVGSVGElement | null = null;
  let bestArea = 0;
  for (const svg of Array.from(root.querySelectorAll("svg"))) {
    if (svg.closest("[data-copy-exclude]")) continue;
    if (svg.hasAttribute("data-chart")) return svg;
    const { width, height } = svg.getBoundingClientRect();
    const area = width * height;
    if (width >= 120 && height >= 60 && area > bestArea) {
      best = svg;
      bestArea = area;
    }
  }
  return best;
}
