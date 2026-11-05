// Shared pixel-grid renderer for the QASight mascot family.
// Each mascot declares a character grid; every char maps to a palette color,
// "." is transparent. shape-rendering keeps edges crisp at any size.

import { MASCOT_PALETTE, type PixelPalette } from "./animations";

export type { PixelPalette };
export { MASCOT_PALETTE };

export interface PixelArtProps {
  grid: string[];
  palette?: PixelPalette;
  /** Rendered width in px; height follows the grid's aspect ratio. */
  size?: number;
  className?: string;
  title?: string;
}

export function PixelArt({ grid, palette = MASCOT_PALETTE, size = 64, className, title }: PixelArtProps) {
  const rows = grid.length;
  const cols = Math.max(0, ...grid.map((row) => row.length));
  const height = cols > 0 ? Math.round((size * rows) / cols) : size;
  return (
    <svg
      viewBox={`0 0 ${cols} ${rows}`}
      width={size}
      height={height}
      shapeRendering="crispEdges"
      role="img"
      aria-label={title ?? "QASight mascot"}
      className={className}
    >
      {title ? <title>{title}</title> : null}
      {grid.flatMap((row, y) =>
        Array.from(row).map((char, x) => {
          const fill = palette[char];
          if (!fill) return null;
          return <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={fill} />;
        })
      )}
    </svg>
  );
}
