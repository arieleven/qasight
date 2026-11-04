import type { PyramidShape } from "../../lib/api-client";

const SHAPE_LABEL: Record<PyramidShape, string> = {
  healthy: "Healthy pyramid",
  top_heavy: "Top-heavy",
  mid_bulge: "Integration-heavy",
  irregular: "Mixed tiers"
};

const SHAPE_TONE: Record<PyramidShape, string> = {
  healthy: "bg-success-400",
  top_heavy: "bg-danger-400",
  mid_bulge: "bg-warn-400",
  irregular: "bg-ink-500"
};

export interface TestPyramidProps {
  unit: number;
  integration: number;
  e2e: number;
  shape: PyramidShape;
  message: string;
}

const TIERS: Array<{ key: "e2e" | "integration" | "unit"; label: string }> = [
  { key: "e2e", label: "E2E" },
  { key: "integration", label: "Integration" },
  { key: "unit", label: "Unit" }
];

/** Stacked-bar test pyramid: bar width is proportional to each tier's test count. */
export function TestPyramid({ unit, integration, e2e, shape, message }: TestPyramidProps) {
  const counts = { unit, integration, e2e };
  const total = unit + integration + e2e;
  const tone = SHAPE_TONE[shape];

  if (total === 0) {
    return <p className="text-sm text-ink-400">No tiered tests yet. Set <code className="font-mono">tier:</code> on a stage in qasight.yaml.</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${tone}`} aria-hidden />
        <span className="text-sm font-medium text-ink-100">{SHAPE_LABEL[shape]}</span>
      </div>
      <div className="space-y-1.5">
        {TIERS.map(({ key, label }) => {
          const value = counts[key];
          const widthPct = Math.max(value > 0 ? 8 : 0, (value / total) * 100);
          return (
            <div key={key} className="flex items-center gap-3">
              <span className="w-20 shrink-0 text-xs text-ink-400">{label}</span>
              {/* Decorative: the count is the text beside it. */}
              <div className="h-4 flex-1 rounded bg-ink-800" aria-hidden>
                <div
                  className={`h-full rounded ${tone}`}
                  style={{ width: `${widthPct}%` }}
                  title={`${label}: ${value}`}
                />
              </div>
              <span className="w-10 shrink-0 text-right font-mono text-xs text-ink-300">{value}</span>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-ink-400">{message}</p>
    </div>
  );
}
