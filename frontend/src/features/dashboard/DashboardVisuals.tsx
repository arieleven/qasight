import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { formatPercent } from "../../lib/format";

export type RunTone = "success" | "danger" | "neutral";

/** Iris, glow and particle colours for each tone; the same green/red as the rest of the UI. */
const ART = {
  success: { iris: "#34d399", deep: "#047857", glow: "52 211 153" },
  danger: { iris: "#f87171", deep: "#b91c1c", glow: "248 113 113" },
  neutral: { iris: "#2dd4bf", deep: "#0f766e", glow: "45 212 191" }
} as const;

// Fixed "random" spots for the floating specks, so the art is the same on every render.
const SPECKS = [
  [62, 48, 1.6], [318, 70, 1.2], [290, 250, 1.8], [96, 268, 1.1], [350, 168, 1.4],
  [40, 160, 1], [250, 30, 1.1], [150, 300, 1.3], [210, 290, 0.9], [20, 90, 1.2]
] as const;

export interface ResultSlice {
  key: "passed" | "failed" | "broken" | "skipped";
  label: string;
  count: number;
}

/** Slice colours: the same pass / fail / warn / grey as everywhere else. */
export const SLICE_COLOR: Record<ResultSlice["key"], string> = {
  passed: "#34d399",
  failed: "#f87171",
  broken: "#fbbf24",
  skipped: "#7e88ae"
};

const IRIS_INNER = 38;
const IRIS_OUTER = 88;

/** One ring slice of the iris, from angle a0 to a1 (radians, 0 at the top, clockwise). */
function slicePath(a0: number, a1: number): string {
  const pt = (r: number, a: number) => `${(180 + Math.sin(a) * r).toFixed(2)} ${(160 - Math.cos(a) * r).toFixed(2)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M ${pt(IRIS_OUTER, a0)} A ${IRIS_OUTER} ${IRIS_OUTER} 0 ${large} 1 ${pt(IRIS_OUTER, a1)} L ${pt(IRIS_INNER, a1)} A ${IRIS_INNER} ${IRIS_INNER} 0 ${large} 0 ${pt(IRIS_INNER, a0)} Z`;
}

/**
 * Original art for the dashboard hero: QASight's eye as a glowing iris,
 * made of rings and fibres. With `slices` (and no fibres) the iris is a pie of the latest
 * run's results (passed, failed, broken, skipped), the colours fading into
 * each other and shaded like a rounded iris, each slice showing its
 * count on hover; without them it takes the run's colour.
 */
export function IrisArt({ tone, slices, className }: { tone: RunTone; slices?: ResultSlice[]; className?: string }) {
  const id = useId().replace(/:/g, "");
  const c = ART[tone];
  const hasSlices = (slices ?? []).some((s) => s.count > 0);
  const fibres = Array.from({ length: 48 }, (_, i) => {
    const a = (i / 48) * Math.PI * 2;
    const inner = 34 + (i % 3) * 2;
    const outer = 86 - (i % 4) * 6;
    return { x1: 180 + Math.cos(a) * inner, y1: 160 + Math.sin(a) * inner, x2: 180 + Math.cos(a) * outer, y2: 160 + Math.sin(a) * outer, o: 0.25 + (i % 5) * 0.1 };
  });
  return (
    <svg viewBox="0 0 360 320" className={className} aria-hidden>
      <defs>
        <radialGradient id={`${id}-halo`} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={`rgb(${c.glow})`} stopOpacity="0.55" />
          <stop offset="55%" stopColor={`rgb(${c.glow})`} stopOpacity="0.12" />
          <stop offset="100%" stopColor={`rgb(${c.glow})`} stopOpacity="0" />
        </radialGradient>
        <radialGradient id={`${id}-iris`} cx="45%" cy="40%" r="60%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.9" />
          <stop offset="18%" stopColor={c.iris} />
          <stop offset="70%" stopColor={c.deep} />
          <stop offset="100%" stopColor="#060b26" />
        </radialGradient>
        <radialGradient id={`${id}-shade`} gradientUnits="userSpaceOnUse" cx="150" cy="122" r="130">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.3" />
          <stop offset="35%" stopColor="#ffffff" stopOpacity="0.08" />
          <stop offset="70%" stopColor="#060b26" stopOpacity="0.15" />
          <stop offset="100%" stopColor="#060b26" stopOpacity="0.7" />
        </radialGradient>
        <radialGradient id={`${id}-depth`} gradientUnits="userSpaceOnUse" cx="180" cy="160" r={IRIS_OUTER}>
          <stop offset={`${(IRIS_INNER - 4) / IRIS_OUTER}`} stopColor="#030616" stopOpacity="0.9" />
          <stop offset={`${(IRIS_INNER + 16) / IRIS_OUTER}`} stopColor="#030616" stopOpacity="0" />
          <stop offset="88%" stopColor="#030616" stopOpacity="0" />
          <stop offset="100%" stopColor="#030616" stopOpacity="0.5" />
        </radialGradient>
        <filter id={`${id}-blur`} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
      </defs>
      <circle cx="180" cy="160" r="160" fill={`url(#${id}-halo)`} />
      {/* Orbit rings, like the lids of a wide-open eye seen from the front. */}
      <ellipse cx="180" cy="160" rx="170" ry="92" fill="none" stroke="white" strokeOpacity="0.10" />
      <ellipse cx="180" cy="160" rx="140" ry="70" fill="none" stroke={c.iris} strokeOpacity="0.35" strokeDasharray="2 6" />
      <circle cx="180" cy="160" r="118" fill="none" stroke="white" strokeOpacity="0.06" />
      {/* The run-coloured rim; the results pie carries its own colours, so it goes without. */}
      {!hasSlices && (
        <>
          <circle cx="180" cy="160" r="100" fill="none" stroke={c.iris} strokeOpacity="0.25" strokeWidth="1.5" />
          <circle cx="180" cy="160" r="92" fill={c.iris} opacity="0.35" filter={`url(#${id}-blur)`} />
        </>
      )}
      <circle cx="180" cy="160" r="88" fill={`url(#${id}-iris)`} />
      {blend(slices).map((seg, i) => (
        <path key={`seg-${i}`} d={seg.d} fill={seg.color} />
      ))}
      {hasSlices && (
        <>
          {/* Light from the top left and a curve into the pupil, so the ring reads as a rounded iris. */}
          <circle cx="180" cy="160" r={IRIS_OUTER} fill={`url(#${id}-shade)`} />
          <circle cx="180" cy="160" r={IRIS_OUTER} fill={`url(#${id}-depth)`} />
          <circle cx="180" cy="160" r={IRIS_OUTER - 1} fill="none" stroke="#060b26" strokeOpacity="0.55" strokeWidth="2" />
        </>
      )}
      {pie(slices).map((slice) => (
        <path
          key={slice.key}
          d={slice.d}
          fill="white"
          fillOpacity="0"
          className="pointer-events-auto transition-[fill-opacity] hover:fill-opacity-10"
          data-testid={`iris-slice-${slice.key}`}
        >
          <title>{`${slice.count} ${slice.label}`}</title>
        </path>
      ))}
      {/* Fibres only on the plain iris; on the results pie they hide the colours. */}
      {!hasSlices && fibres.map((f, i) => (
        <line key={i} x1={f.x1} y1={f.y1} x2={f.x2} y2={f.y2} stroke="white" strokeOpacity={f.o} strokeWidth="1" strokeLinecap="round" />
      ))}
      <circle cx="180" cy="160" r="34" fill="#030616" />
      <circle cx="180" cy="160" r="34" fill="none" stroke={hasSlices ? "white" : c.iris} strokeOpacity={hasSlices ? 0.12 : 0.6} strokeWidth="2" />
      <ellipse cx="164" cy="142" rx="11" ry="7" fill="white" opacity="0.85" transform="rotate(-30 164 142)" />
      <circle cx="196" cy="176" r="3" fill="white" opacity="0.5" />
      {SPECKS.map(([x, y, r], i) => (
        <circle key={i} cx={x} cy={y} r={r} fill={i % 3 ? "white" : c.iris} opacity={0.35 + (i % 4) * 0.12} />
      ))}
    </svg>
  );
}

/** Each non-empty slice's start and end angle; every slice gets at least a sliver, so one broken test out of a thousand still shows. */
function pieAngles(slices: ResultSlice[] | undefined) {
  const shown = (slices ?? []).filter((s) => s.count > 0);
  const total = shown.reduce((n, s) => n + s.count, 0);
  if (total === 0) return [];
  const minAngle = 0.06;
  const raw = shown.map((s) => Math.max((s.count / total) * 2 * Math.PI, minAngle));
  const scale = (2 * Math.PI) / raw.reduce((a, b) => a + b, 0);
  let a = 0;
  return shown.map((s, i) => {
    const a0 = a;
    a += raw[i] * scale;
    return { ...s, a0, a1: a };
  });
}

/** Turns counts into ring slices (used as hover targets); a single non-empty slice is drawn as a full ring. */
function pie(slices: ResultSlice[] | undefined) {
  const shown = pieAngles(slices);
  if (shown.length === 1) {
    // Two half arcs, since one SVG arc can't draw a whole circle.
    return [{ ...shown[0], d: `${slicePath(0, Math.PI)} ${slicePath(Math.PI, 2 * Math.PI)}` }];
  }
  return shown.map((s) => ({ ...s, d: slicePath(s.a0, s.a1) }));
}

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Shortest distance between two angles on the circle. */
function angleGap(a: number, b: number): number {
  const d = Math.abs(a - b) % (2 * Math.PI);
  return Math.min(d, 2 * Math.PI - d);
}

/**
 * The pie as many thin wedges whose colours fade from one slice into the
 * next (SVG has no conic gradient). Each slice keeps its pure colour in its
 * middle and blends over a soft band at each edge.
 */
function blend(slices: ResultSlice[] | undefined) {
  const shown = pieAngles(slices);
  if (shown.length === 0) return [];
  const steps = 144;
  const step = (2 * Math.PI) / steps;
  return Array.from({ length: steps }, (_, i) => {
    const mid = (i + 0.5) * step;
    let r = 0, g = 0, b = 0, total = 0;
    for (const s of shown) {
      const span = s.a1 - s.a0;
      const feather = Math.min(0.45, Math.max(span * 0.6, 0.12));
      const inside = span >= 2 * Math.PI - 1e-6 || (((mid - s.a0) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI) <= span;
      const dist = inside ? 0 : Math.min(angleGap(mid, s.a0), angleGap(mid, s.a1));
      const t = Math.max(0, 1 - dist / feather);
      const w = t * t * (3 - 2 * t);
      if (w <= 0) continue;
      const [cr, cg, cb] = hexToRgb(SLICE_COLOR[s.key]);
      r += cr * w; g += cg * w; b += cb * w; total += w;
    }
    const color = `rgb(${Math.round(r / total)} ${Math.round(g / total)} ${Math.round(b / total)})`;
    // A hair of overlap hides the seams between wedges.
    return { color, d: slicePath(i * step, Math.min((i + 1) * step + 0.01, 2 * Math.PI)) };
  });
}

/**
 * A big gauge: an open ring (three quarters of a circle) that fills to
 * `value` out of 100, with whatever is passed as children in its middle.
 */
export function Gauge({ value, color, children, size = 168 }: { value: number | null; color: string; children: ReactNode; size?: number }) {
  const r = 70;
  const sweep = 0.75; // the ring is open at the bottom
  const circumference = 2 * Math.PI * r;
  const track = circumference * sweep;
  const filled = value == null ? 0 : (Math.max(0, Math.min(100, value)) / 100) * track;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox="0 0 168 168" width={size} height={size} className="rotate-[135deg]" aria-hidden>
        <circle cx="84" cy="84" r={r} fill="none" stroke="rgb(255 255 255 / 0.08)" strokeWidth="12" strokeLinecap="round" strokeDasharray={`${track} ${circumference}`} />
        <circle
          cx="84"
          cy="84"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circumference}`}
          style={{ filter: `drop-shadow(0 0 6px ${color})`, transition: "stroke-dasharray 700ms ease-out" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">{children}</div>
    </div>
  );
}

export interface RunBar {
  run_id: string;
  label: string;
  health_pct: number | null;
  tone: RunTone;
}

const BAR_FILL: Record<RunTone, string> = {
  success: "from-success-600 to-success-300",
  danger: "from-danger-600 to-danger-300",
  neutral: "from-ink-600 to-ink-400"
};

/**
 * The last runs as a row of bars, oldest on the left: height is the share
 * of tests that passed, colour is the run's result. Each bar opens its run.
 */
export function RunBars({ runs }: { runs: RunBar[] }) {
  if (runs.length === 0) return null;
  return (
    <div className="inset-tile px-4 pb-3 pt-4" data-testid="run-bars">
      <div className="relative flex h-32 items-end gap-2 sm:gap-3">
        {[100, 50].map((line) => (
          <div key={line} className="pointer-events-none absolute inset-x-0 border-t border-dashed border-white/5" style={{ bottom: `${line}%` }} aria-hidden />
        ))}
        {runs.map((run) => {
          const pct = run.health_pct == null ? 0 : Math.max(0, Math.min(100, run.health_pct));
          return (
            <Link
              key={run.run_id}
              to={`/runs/${run.run_id}`}
              className="group relative flex h-full flex-1 items-end justify-center"
              aria-label={`${run.label}: ${run.health_pct == null ? "no tests" : `${formatPercent(run.health_pct)} passed`}`}
              title={`${run.label} · ${run.health_pct == null ? "no tests" : `${formatPercent(run.health_pct)} passed`}`}
            >
              <span
                className={`block w-full max-w-[18px] rounded-full bg-gradient-to-t transition-[filter] group-hover:brightness-125 ${BAR_FILL[run.tone]}`}
                style={{ height: `${Math.max(pct, 4)}%` }}
              />
            </Link>
          );
        })}
      </div>
      <p className="mt-2 flex justify-between text-[11px] text-ink-400">
        <span>older</span>
        <span>pass rate per run</span>
        <span>latest</span>
      </p>
    </div>
  );
}
