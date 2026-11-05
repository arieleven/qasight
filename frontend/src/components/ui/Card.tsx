import { useId, useRef, useState, type HTMLAttributes, type ReactNode } from "react";

import { CopyMenu, cardAnchor } from "./CopyMenu";

export interface CardProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title?: ReactNode;
  actions?: ReactNode;
  /** Show the Copy menu (text, table, CSV, chart image, link) next to the title. Default on. */
  copy?: boolean;
  /** Click the title to open or close the card. Remembered per card in this browser. */
  collapsible?: boolean;
  /** Whether a collapsible card starts open, before the viewer has opened or closed it. Default true. */
  defaultOpen?: boolean;
  /** One line shown next to the title while a collapsible card is closed, so it still says something. */
  collapsedSummary?: ReactNode;
}

const OPEN_KEY = "qasight.cardOpen";

function loadOpen(anchor: string | undefined): boolean | undefined {
  if (!anchor) return undefined;
  try {
    const value = (JSON.parse(localStorage.getItem(OPEN_KEY) ?? "{}") as Record<string, boolean>)[anchor];
    return typeof value === "boolean" ? value : undefined;
  } catch {
    return undefined; // storage disabled or a bad value: use the default
  }
}

function saveOpen(anchor: string | undefined, open: boolean) {
  if (!anchor) return;
  try {
    const all = JSON.parse(localStorage.getItem(OPEN_KEY) ?? "{}") as Record<string, boolean>;
    localStorage.setItem(OPEN_KEY, JSON.stringify({ ...all, [anchor]: open }));
  } catch {
    // Not remembered between visits; the toggle still works.
  }
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={`h-4 w-4 shrink-0 text-ink-400 transition-transform ${open ? "rotate-180" : ""}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function Card({
  title,
  actions,
  className,
  children,
  copy = true,
  collapsible = false,
  defaultOpen = true,
  collapsedSummary,
  id,
  ...props
}: CardProps) {
  const ref = useRef<HTMLDivElement>(null);
  const bodyId = useId();
  const titleText = typeof title === "string" ? title : undefined;
  const anchor = id ?? (titleText ? cardAnchor(titleText) : undefined);
  const [open, setOpen] = useState(() => (collapsible ? (loadOpen(anchor) ?? defaultOpen) : true));
  const toggle = () => {
    setOpen(!open);
    saveOpen(anchor, !open);
  };

  const heading = title ? <h2 className="text-base font-bold text-white">{title}</h2> : <span />;
  return (
    <div ref={ref} id={anchor} className={`glass-card p-5 ${className ?? ""}`} {...props}>
      {(title || actions) && (
        <div className={`flex items-center justify-between gap-2 ${open ? "mb-4" : ""}`}>
          {collapsible && title ? (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={bodyId}
              onClick={toggle}
              className="-my-1 flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-1 py-1 text-left"
            >
              <Chevron open={open} />
              {heading}
              {!open && collapsedSummary ? <span className="text-sm text-ink-300">{collapsedSummary}</span> : null}
            </button>
          ) : (
            heading
          )}
          <div className="flex items-center gap-2">
            {open ? actions : null}
            {title && copy ? <CopyMenu target={ref} label={titleText} anchor={anchor} /> : null}
          </div>
        </div>
      )}
      {!collapsible ? children : open ? <div id={bodyId}>{children}</div> : null}
    </div>
  );
}
