import { Link } from "react-router-dom";

import { type HealthFindingItem, type ProjectHealthResponse } from "../../lib/api-client";
import { Badge } from "../../components/ui";
import { testPath } from "../../lib/format";
import { TONE_BAR, TONE_TEXT, scoreTone } from "./score";

// The parts of the Test health score, shared by its page and the Overview's open score card.

export function Signals({ signals }: { signals: ProjectHealthResponse["signals"] }) {
  return (
    <ul className="space-y-3">
      {signals.map((signal) => {
        const tone = scoreTone(signal.score);
        return (
          <li key={signal.key}>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="text-sm font-semibold text-ink-100">
                {signal.name} <span className="text-xs font-normal text-ink-400">· weight {signal.weight}%</span>
              </span>
              <span className={`text-sm font-semibold ${TONE_TEXT[tone]}`}>{signal.score ?? "No data"}</span>
            </div>
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-800" aria-hidden>
              <div className={`h-full rounded-full ${TONE_BAR[tone]}`} style={{ width: `${signal.score ?? 0}%` }} />
            </div>
            <p className="mt-1 text-xs text-ink-300">{signal.value}</p>
            <p className="text-xs text-ink-400">{signal.how}</p>
          </li>
        );
      })}
    </ul>
  );
}

const FINDING_BADGE: Record<HealthFindingItem["kind"], { label: string; tone: "danger" | "warn" | "neutral" }> = {
  red_cycle: { label: "Red", tone: "danger" },
  flaky: { label: "Flaky", tone: "warn" },
  slower: { label: "Slower", tone: "warn" },
  quarantined: { label: "Quarantined", tone: "neutral" },
  skipped: { label: "Skipped", tone: "neutral" }
};

function findingLink(finding: HealthFindingItem): string | null {
  if (finding.test_key) return testPath(finding.cycle, finding.test_key);
  if (finding.run_id) return `/runs/${finding.run_id}`;
  return null;
}

export function Findings({ findings }: { findings: HealthFindingItem[] }) {
  if (findings.length === 0) {
    return <p className="text-sm text-success-300">Nothing to fix: no red cycles, flaky, slower, skipped or quarantined tests.</p>;
  }
  return (
    <ul className="divide-fade">
      {findings.map((finding, index) => {
        const badge = FINDING_BADGE[finding.kind];
        const to = findingLink(finding);
        return (
          <li key={`${finding.kind}:${finding.cycle}:${finding.title}:${index}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 py-2.5 first:pt-0">
            <Badge tone={badge.tone}>{badge.label}</Badge>
            {to ? (
              <Link to={to} className="-my-1 inline-block break-all py-1 font-medium text-brand-300 hover:underline">
                {finding.title}
              </Link>
            ) : (
              <span className="font-medium text-ink-100">{finding.title}</span>
            )}
            <span className="text-xs text-ink-400">
              {finding.cycle} · {finding.detail}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function ByCycle({ rows }: { rows: ProjectHealthResponse["by_cycle"] }) {
  return (
    <div className="-mx-5 overflow-x-auto px-5">
      <table className="w-full min-w-[34rem] text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-ink-400">
          <tr>
            <th scope="col" className="pb-2 font-semibold">Cycle</th>
            <th scope="col" className="pb-2 font-semibold">Score</th>
            <th scope="col" className="pb-2 font-semibold">Latest run</th>
            <th scope="col" className="pb-2 font-semibold">Green runs</th>
            <th scope="col" className="pb-2 font-semibold">Flaky</th>
            <th scope="col" className="pb-2 font-semibold">Slower</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((row) => (
            <tr key={row.cycle}>
              <td className="py-2 pr-3">
                <Link to={`/test-health?cycle=${encodeURIComponent(row.cycle)}`} className="font-mono text-brand-300 hover:underline">
                  {row.cycle}
                </Link>
              </td>
              <td className={`py-2 pr-3 font-semibold ${TONE_TEXT[scoreTone(row.score)]}`}>{row.score ?? "–"}</td>
              <td className="py-2 pr-3">
                <Link to={`/runs/${row.run_id}`} className="hover:underline">
                  {row.passed_latest ? (
                    <span className="text-success-300">Passed</span>
                  ) : (
                    <span className="text-danger-300">{row.failing} failing</span>
                  )}
                </Link>
              </td>
              <td className="py-2 pr-3 text-ink-200">
                {row.green_runs} of {row.runs}
              </td>
              <td className="py-2 pr-3 text-ink-200">{row.flaky}</td>
              <td className="py-2 text-ink-200">{row.slower}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
