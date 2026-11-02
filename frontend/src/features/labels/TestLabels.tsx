import { Link } from "react-router-dom";

import type { TestLabelItem, TestLinkItem } from "../../lib/api-client";
import { cyclePath } from "../../lib/format";
import { labelFilter, labelText } from "./labels";

const CHIP =
  "inline-flex items-center rounded-full border border-white/10 bg-ink-800/70 px-2 py-0.5 text-[11px] font-medium text-ink-200";

/**
 * A test's labels (feature, severity, tag...) as chips and its issue links.
 * With `cycle`, each chip links to the cycle's tests that carry the same label.
 */
export function TestLabels({
  labels,
  links,
  cycle
}: {
  labels?: TestLabelItem[];
  links?: TestLinkItem[];
  cycle?: string | null;
}) {
  const shownLabels = labels ?? [];
  const shownLinks = links ?? [];
  if (shownLabels.length === 0 && shownLinks.length === 0) return null;
  return (
    <ul className="flex flex-wrap items-center gap-1.5" aria-label="Labels and links">
      {shownLabels.map((label) => (
        <li key={labelFilter(label)}>
          {cycle ? (
            <Link
              to={`${cyclePath(cycle)}?label=${encodeURIComponent(labelFilter(label))}#tests`}
              className={`${CHIP} hover:border-brand-500 hover:text-brand-300`}
              title={`Tests in ${cycle} with ${labelText(label)}`}
            >
              {labelText(label)}
            </Link>
          ) : (
            <span className={CHIP}>{labelText(label)}</span>
          )}
        </li>
      ))}
      {shownLinks.map((link) => (
        <li key={link.url}>
          <a
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] font-medium text-brand-300 hover:underline"
            title={link.url}
          >
            {link.type === "issue" ? `Issue ${link.name}` : link.name}
          </a>
        </li>
      ))}
    </ul>
  );
}
