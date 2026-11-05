import { useState } from "react";

import { Button } from "./Button";

/** Rows a long list shows before "Show all": enough to answer "what's on top?" without a wall of rows. */
export const FIRST_ROWS = 3;

/**
 * The first `limit` items of a list, and whether the rest are showing. Pair it
 * with <ShowMoreButton> so every long list in the UI opens the same way.
 */
export function useShowMore<T>(items: readonly T[], limit: number = FIRST_ROWS) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, limit);
  return { visible, expanded, setExpanded, hidden: items.length - visible.length, total: items.length, limit };
}

export function ShowMoreButton({
  total,
  limit = FIRST_ROWS,
  expanded,
  onToggle,
  className
}: {
  total: number;
  limit?: number;
  expanded: boolean;
  onToggle: (expanded: boolean) => void;
  className?: string;
}) {
  if (total <= limit) return null;
  return (
    <Button variant="secondary" size="sm" className={className} aria-expanded={expanded} onClick={() => onToggle(!expanded)}>
      {expanded ? "Show fewer" : `Show all ${total}`}
    </Button>
  );
}
