/**
 * The report is a printed document, so it has its own light palette, set as
 * plain colours (not Tailwind classes) so charts keep them when copied as
 * images or rendered to PDF, PNG and slides.
 */
export const PAPER = {
  ink: "#101828",
  muted: "#475467",
  faint: "#98a2b3",
  rule: "#e4e7ec",
  wash: "#f2f4f7",
  brand: "#1d4ed8",
  brandSoft: "#bfdbfe",
  good: "#067647",
  goodSoft: "#d1fadf",
  bad: "#b42318",
  badSoft: "#fee4e2",
  warn: "#b54708",
  warnSoft: "#fef0c7"
} as const;
