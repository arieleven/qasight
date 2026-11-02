import { useEffect, useRef, useState, type RefObject } from "react";

import {
  copyImage,
  copyRich,
  copyText,
  downloadBlob,
  findChart,
  slugify,
  tableMatrix,
  textToHtml,
  toCsv,
  toHtmlTable,
  toTsv,
  svgToPng,
  visibleText
} from "../../lib/clipboard";

interface Action {
  id: string;
  label: string;
  run: () => Promise<string>;
}

export interface CopyMenuProps {
  /** The element whose content is copied (usually the card). */
  target: RefObject<HTMLElement | null>;
  /** What the copy is called (the card title), for the pasted heading and file names; defaults to the target's first heading. */
  label?: string;
  /** Anchor id for "Copy link"; no link item without one. */
  anchor?: string;
}

const FAILED = "Your browser blocked the clipboard. Use HTTPS or localhost, or select the text instead.";

function actionsFor(root: HTMLElement, label: string, anchor?: string): Action[] {
  const actions: Action[] = [];
  const rows = tableMatrix(root);
  actions.push({
    id: "text",
    label: "Copy as text",
    run: async () => {
      const text = visibleText(root);
      return (await copyRich(textToHtml(text), text)) ? "Copied" : FAILED;
    }
  });
  if (rows) {
    actions.push({
      id: "table",
      label: "Copy as table",
      run: async () => ((await copyRich(toHtmlTable(rows, label), toTsv(rows))) ? "Table copied" : FAILED)
    });
    actions.push({
      id: "csv",
      label: "Download CSV",
      run: async () => {
        downloadBlob(`${slugify(label)}.csv`, new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" }));
        return "CSV downloaded";
      }
    });
  }
  const chart = findChart(root);
  if (chart) {
    actions.push({
      id: "image",
      label: "Copy chart as image",
      run: async () => {
        const png = await svgToPng(chart);
        return (await copyImage(png)) ? "Chart copied" : FAILED;
      }
    });
    actions.push({
      id: "png",
      label: "Download chart (PNG)",
      run: async () => {
        downloadBlob(`${slugify(label)}.png`, await svgToPng(chart));
        return "Chart downloaded";
      }
    });
  }
  if (anchor) {
    actions.push({
      id: "link",
      label: "Copy link to this card",
      run: async () => {
        const url = new URL(window.location.href);
        url.hash = anchor;
        return (await copyText(url.toString())) ? "Link copied" : FAILED;
      }
    });
  }
  return actions;
}

/** A small "Copy" menu for a card: text, table, CSV, chart image, link. */
export function CopyMenu({ target, label, anchor }: CopyMenuProps) {
  const [open, setOpen] = useState(false);
  const [actions, setActions] = useState<Action[]>([]);
  const [status, setStatus] = useState("");
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (!status) return;
    const t = setTimeout(() => setStatus(""), 2500);
    return () => clearTimeout(t);
  }, [status]);

  const name = label ?? "card";
  const toggle = () => {
    const root = target.current;
    if (!open && root) {
      const heading = label ?? root.querySelector("h1, h2, h3")?.textContent?.trim() ?? "results";
      setActions(actionsFor(root, heading, anchor));
    }
    setOpen((v) => !v);
  };

  const run = async (action: Action) => {
    setOpen(false);
    try {
      setStatus(await action.run());
    } catch {
      setStatus(FAILED);
    }
  };

  return (
    <div ref={wrap} className="relative inline-flex items-center gap-2 print:hidden" data-copy-exclude>
      <span aria-live="polite" className="text-xs text-ink-300">
        {status}
      </span>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Copy or export ${name}`}
        title="Copy or export"
        onClick={toggle}
        className="rounded-lg px-1.5 py-1 text-ink-300 transition-colors hover:bg-ink-800 hover:text-ink-100"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="9" y="9" width="11" height="11" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
        </svg>
      </button>
      {open ? (
        <div
          role="menu"
          aria-label={`Copy ${name}`}
          className="absolute right-0 top-full z-30 mt-1 min-w-48 rounded-xl border border-white/10 bg-ink-900 p-1 shadow-xl"
        >
          {actions.map((a) => (
            <button
              key={a.id}
              type="button"
              role="menuitem"
              onClick={() => void run(a)}
              className="block w-full rounded-lg px-3 py-1.5 text-left text-sm text-ink-100 hover:bg-ink-800"
            >
              {a.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** The card anchor for a title: `"Failing tests"` → `"card-failing-tests"`. */
export function cardAnchor(title: string): string {
  return `card-${slugify(title)}`;
}
