import { BRAND_MARKS } from "./brandMarks";

/** Black or white, whichever reads better on `hex`. */
function inkOn(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return luminance > 0.4 ? "#0B0F14" : "#FFFFFF";
}

/** A tool's own logo on a tile in its brand colour; nothing for a tool without one (no made-up logos). */
export function ToolLogo({ tool, name, size = 36 }: { tool: string; name: string; size?: number }) {
  const mark = BRAND_MARKS[tool];
  if (!mark) return null;
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-xl shadow-sm ring-1 ring-white/10"
      style={{ width: size, height: size, backgroundColor: mark.hex }}
      role="img"
      aria-label={`${name} logo`}
      data-testid={`tool-logo-${tool}`}
    >
      <svg viewBox="0 0 24 24" width={size * 0.58} height={size * 0.58} fill={inkOn(mark.hex)} aria-hidden>
        <path d={mark.path} />
      </svg>
    </span>
  );
}
