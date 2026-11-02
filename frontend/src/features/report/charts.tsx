import type { ManagerReport } from "../../lib/api-client";
import { PAPER } from "./palette";

type Point = ManagerReport["trend"][number];

const W = 420;
const H = 200;
const PAD = { top: 14, right: 12, bottom: 28, left: 40 };

function dayLabel(day: string, many: boolean): string {
  const [, m, d] = day.split("-").map(Number);
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1];
  return many ? `${d}/${m}` : `${month} ${d}`;
}

/** Every n-th label so they don't collide (about 8 on the axis). */
function labelStep(n: number): number {
  return Math.max(1, Math.ceil(n / 6));
}

/**
 * Pass rate per day as a line, on a y-axis that starts near the lowest value so
 * a drop from 99% to 95% is visible (the axis says where it starts).
 */
export function PassRateChart({ points }: { points: Point[] }) {
  const known = points.filter((p) => p.pass_rate !== null);
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const min = known.length ? Math.max(0, Math.floor(Math.min(...known.map((p) => p.pass_rate as number)) / 5) * 5 - 5) : 0;
  const max = 100;
  const x = (i: number) => PAD.left + (points.length <= 1 ? innerW / 2 : (i * innerW) / (points.length - 1));
  const y = (v: number) => PAD.top + innerH - ((v - min) / (max - min || 1)) * innerH;
  const segments: string[] = [];
  let current: string[] = [];
  points.forEach((p, i) => {
    if (p.pass_rate === null) {
      if (current.length) segments.push(current.join(" "));
      current = [];
    } else {
      current.push(`${x(i).toFixed(1)},${y(p.pass_rate).toFixed(1)}`);
    }
  });
  if (current.length) segments.push(current.join(" "));
  const ticks = [min, (min + max) / 2, max];
  const step = labelStep(points.length);
  const last = known[known.length - 1];
  return (
    <svg
      data-chart
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      role="img"
      aria-label={
        known.length
          ? `Test pass rate per day, from ${known[0].pass_rate}% on ${known[0].day} to ${last.pass_rate}% on ${last.day}`
          : "Test pass rate per day: no runs in this period"
      }
      style={{ fontFamily: "Inter, system-ui, sans-serif" }}
    >
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke={PAPER.rule} strokeWidth={1} />
          <text x={PAD.left - 6} y={y(t)} fill={PAPER.muted} fontSize={12} textAnchor="end" dominantBaseline="middle">
            {Math.round(t)}%
          </text>
        </g>
      ))}
      {segments.map((pts) => (
        <polyline key={pts} points={pts} fill="none" stroke={PAPER.brand} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
      ))}
      {points.map((p, i) =>
        p.pass_rate === null ? null : <circle key={p.day} cx={x(i)} cy={y(p.pass_rate)} r={points.length > 31 ? 2 : 3.5} fill={PAPER.brand} />
      )}
      {points.map((p, i) =>
        i % step === 0 || i === points.length - 1 ? (
          <text
            key={`l-${p.day}`}
            x={x(i)}
            y={H - 8}
            fill={PAPER.muted}
            fontSize={12}
            textAnchor={points.length > 1 && i === points.length - 1 ? "end" : i === 0 && points.length > 1 ? "start" : "middle"}
          >
            {dayLabel(p.day, points.length > 14)}
          </text>
        ) : null
      )}
      {!known.length ? (
        <text x={W / 2} y={H / 2} fill={PAPER.faint} fontSize={13} textAnchor="middle">
          No runs in this period
        </text>
      ) : null}
    </svg>
  );
}

/** Runs per day as stacked bars: green runs below, red runs above. */
export function RunsChart({ points }: { points: Point[] }) {
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const top = Math.max(1, ...points.map((p) => p.runs));
  const slot = innerW / Math.max(1, points.length);
  const bar = Math.max(2, Math.min(36, slot * 0.6));
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH;
  const step = labelStep(points.length);
  const total = points.reduce((n, p) => n + p.runs, 0);
  const green = points.reduce((n, p) => n + p.green, 0);
  return (
    <svg
      data-chart
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      role="img"
      aria-label={`Runs per day: ${total} runs, ${green} green, ${total - green} red`}
      style={{ fontFamily: "Inter, system-ui, sans-serif" }}
    >
      {[0, top].map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke={PAPER.rule} strokeWidth={1} />
          <text x={PAD.left - 6} y={y(t)} fill={PAPER.muted} fontSize={12} textAnchor="end" dominantBaseline="middle">
            {t}
          </text>
        </g>
      ))}
      {points.map((p, i) => {
        const cx = PAD.left + slot * i + slot / 2;
        const red = p.runs - p.green;
        return (
          <g key={p.day}>
            {p.green ? <rect x={cx - bar / 2} y={y(p.green)} width={bar} height={y(0) - y(p.green)} fill={PAPER.good} rx={2} /> : null}
            {red ? <rect x={cx - bar / 2} y={y(p.runs)} width={bar} height={y(p.green) - y(p.runs)} fill={PAPER.bad} rx={2} /> : null}
            {i % step === 0 || i === points.length - 1 ? (
              <text x={cx} y={H - 8} fill={PAPER.muted} fontSize={12} textAnchor={i === points.length - 1 && points.length > 7 ? "end" : "middle"}>
                {dayLabel(p.day, points.length > 14)}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
