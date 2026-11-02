import { useRef, type ReactNode } from "react";

import { CopyMenu, cardAnchor } from "../../components/ui";
import type { ManagerReport } from "../../lib/api-client";
import { PassRateChart, RunsChart } from "./charts";
import { PAPER } from "./palette";

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

function fmtPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "–" : `${v.toFixed(1)}%`;
}

/** "+1.2 pts" / "−0.4 pts" / "no change", against the period before. */
function delta(now: number | null, before: number | null, unit = " pts"): { text: string; color: string } | null {
  if (now === null || before === null) return null;
  const d = now - before;
  if (Math.abs(d) < 0.05) return { text: "no change", color: PAPER.muted };
  return { text: `${d > 0 ? "+" : "−"}${Math.abs(d).toFixed(1)}${unit}`, color: d > 0 ? PAPER.good : PAPER.bad };
}

function scoreColor(score: number | null): string {
  if (score === null) return PAPER.muted;
  return score >= 80 ? PAPER.good : score >= 60 ? PAPER.warn : PAPER.bad;
}

export function periodRange(report: ManagerReport): string {
  return report.days <= 1 ? fmtDate(report.end) : `${fmtDate(report.start)} – ${fmtDate(report.end)}`;
}

/** A titled block of the report with its own Copy menu; kept on one printed page when it fits. */
function Section({ title, children, note, copy = true }: { title: string; children: ReactNode; note?: string; copy?: boolean }) {
  const ref = useRef<HTMLElement>(null);
  const anchor = cardAnchor(title);
  return (
    <section ref={ref} id={anchor} className="report-section mt-8 break-inside-avoid">
      <div className="mb-3 flex items-baseline justify-between gap-3 border-b pb-1.5" style={{ borderColor: PAPER.rule }}>
        <h2 className="text-base font-bold" style={{ color: PAPER.ink }}>
          {title}
        </h2>
        <div className="flex items-center gap-2">
          {note ? (
            <span className="text-xs" style={{ color: PAPER.muted }}>
              {note}
            </span>
          ) : null}
          {copy ? <CopyMenu target={ref} label={title} anchor={anchor} /> : null}
        </div>
      </div>
      {children}
    </section>
  );
}

function Kpi({ label, value, sub, accent }: { label: string; value: string; sub?: ReactNode; accent?: string }) {
  return (
    <div className="rounded-xl border p-4" style={{ borderColor: PAPER.rule, background: PAPER.wash }}>
      <p className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: PAPER.muted }}>
        {label}
      </p>
      <p className="mt-1 text-3xl font-bold tabular-nums" style={{ color: accent ?? PAPER.ink }}>
        {value}
      </p>
      {sub ? (
        <p className="mt-1 text-xs" style={{ color: PAPER.muted }}>
          {sub}
        </p>
      ) : null}
    </div>
  );
}

function TestList({ items, empty }: { items: ManagerReport["still_failing"]; empty: string }) {
  if (!items.length)
    return (
      <p className="text-sm" style={{ color: PAPER.muted }}>
        {empty}
      </p>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs" style={{ color: PAPER.muted }}>
            <th className="py-1 pr-3 font-semibold">Test</th>
            <th className="py-1 pr-3 font-semibold">Cycle</th>
            {items.some((t) => t.detail) ? <th className="py-1 font-semibold">Change</th> : null}
          </tr>
        </thead>
        <tbody>
          {items.map((t) => (
            <tr key={`${t.cycle}:${t.name}`} className="border-t align-top" style={{ borderColor: PAPER.rule, color: PAPER.ink }}>
              <td className="py-1.5 pr-3 font-mono text-[12px] break-all">{t.name}</td>
              <td className="py-1.5 pr-3">{t.cycle}</td>
              {items.some((x) => x.detail) ? <td className="py-1.5 tabular-nums">{t.detail}</td> : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The manager report as a printable page: white paper, dark text, charts in
 * fixed colours. The same component is what `testo export` renders to PDF,
 * PNG and HTML, so the screen, the printout and the files match.
 */
export function ReportDocument({ report, brand = "Testosterone", copy = true }: { report: ManagerReport; brand?: string; copy?: boolean }) {
  const k = report.kpis;
  const rate = delta(k.pass_rate, k.prev_pass_rate);
  const cov = delta(k.coverage_pct, k.prev_coverage_pct);
  const greenPct = k.runs ? Math.round((100 * k.green) / k.runs) : null;
  const prevGreenPct = k.prev_runs ? Math.round((100 * k.prev_green) / k.prev_runs) : null;
  const greenDelta = delta(greenPct, prevGreenPct);
  const scope = report.scope.length ? report.scope.join(", ") : "All cycles";
  const previous = report.days === 1 ? "the day before" : `the previous ${report.days} days`;
  return (
    <article
      data-report-ready
      className="report-paper mx-auto w-full max-w-[880px] rounded-2xl bg-white px-5 py-8 shadow-2xl sm:px-10 sm:py-10"
      style={{ color: PAPER.ink, fontFamily: "Inter, system-ui, -apple-system, Segoe UI, sans-serif" }}
    >
      <header className="flex flex-wrap items-start justify-between gap-4 border-b-4 pb-4" style={{ borderColor: PAPER.brand }}>
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.2em]" style={{ color: PAPER.brand }}>
            {brand}
          </p>
          <h1 className="mt-1 text-2xl font-bold sm:text-3xl" style={{ color: PAPER.ink }}>
            {report.title}
          </h1>
          <p className="mt-1 text-sm" style={{ color: PAPER.muted }}>
            {periodRange(report)} · {scope}
          </p>
        </div>
        <p className="text-left text-xs sm:text-right" style={{ color: PAPER.muted }}>
          Generated {new Date(report.generated_at * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
          <br />
          Compared with {previous}
        </p>
      </header>

      <section className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4 break-inside-avoid" aria-label="Headline numbers">
        <Kpi
          label="Test health"
          value={k.health_score === null ? "–" : String(k.health_score)}
          accent={scoreColor(k.health_score)}
          sub={k.health_grade ? `Grade ${k.health_grade} · out of 100` : "No runs scored yet"}
        />
        <Kpi
          label="Tests passing"
          value={fmtPct(k.pass_rate)}
          sub={rate ? <span style={{ color: rate.color }}>{rate.text}</span> : "No earlier period"}
        />
        <Kpi
          label="Green builds"
          value={greenPct === null ? "–" : `${greenPct}%`}
          sub={
            <>
              {k.green} of {k.runs} runs
              {greenDelta ? <span style={{ color: greenDelta.color }}> · {greenDelta.text}</span> : null}
            </>
          }
        />
        {k.coverage_pct !== null ? (
          <Kpi label="Line coverage" value={fmtPct(k.coverage_pct)} sub={cov ? <span style={{ color: cov.color }}>{cov.text}</span> : "No earlier period"} />
        ) : (
          <Kpi
            label="Needs attention"
            value={String(k.failing_tests + k.newly_flaky)}
            accent={k.failing_tests + k.newly_flaky ? PAPER.bad : PAPER.good}
            sub={`${k.failing_tests} failing · ${k.newly_flaky} newly flaky`}
          />
        )}
      </section>

      <Section copy={copy} title="Summary">
        <ul className="list-disc space-y-1 pl-5 text-[15px] leading-relaxed" style={{ color: PAPER.ink }}>
          {report.summary.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </Section>

      <div className="grid gap-x-8 md:grid-cols-2">
        <Section copy={copy} title="Pass rate by day">
          <PassRateChart points={report.trend} />
        </Section>
        <Section copy={copy} title="Runs by day" note="green · red">
          <RunsChart points={report.trend} />
        </Section>
      </div>

      <Section copy={copy} title="Cycles">
        {report.cycles.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="text-left text-xs" style={{ color: PAPER.muted }}>
                  <th className="py-1 pr-3 font-semibold">Cycle</th>
                  <th className="py-1 pr-3 text-right font-semibold">Runs</th>
                  <th className="py-1 pr-3 text-right font-semibold">Green</th>
                  <th className="py-1 pr-3 text-right font-semibold">Pass rate</th>
                  <th className="py-1 pr-3 text-right font-semibold">Change</th>
                  <th className="py-1 pr-3 text-right font-semibold">Failing</th>
                  <th className="py-1 pr-3 text-right font-semibold">New flaky</th>
                  <th className="py-1 text-right font-semibold">Health</th>
                </tr>
              </thead>
              <tbody>
                {report.cycles.map((c) => {
                  const d = delta(c.pass_rate, c.prev_pass_rate);
                  return (
                    <tr key={c.cycle} className="border-t tabular-nums" style={{ borderColor: PAPER.rule }}>
                      <td className="py-1.5 pr-3 font-medium">{c.cycle}</td>
                      <td className="py-1.5 pr-3 text-right">{c.runs}</td>
                      <td className="py-1.5 pr-3 text-right">{c.green}</td>
                      <td className="py-1.5 pr-3 text-right">{fmtPct(c.pass_rate)}</td>
                      <td className="py-1.5 pr-3 text-right" style={{ color: d?.color ?? PAPER.muted }}>
                        {d?.text ?? "–"}
                      </td>
                      <td className="py-1.5 pr-3 text-right" style={{ color: c.failing ? PAPER.bad : undefined }}>
                        {c.failing}
                      </td>
                      <td className="py-1.5 pr-3 text-right" style={{ color: c.newly_flaky ? PAPER.warn : undefined }}>
                        {c.newly_flaky}
                      </td>
                      <td className="py-1.5 text-right font-semibold" style={{ color: scoreColor(c.health_score) }}>
                        {c.health_score ?? "–"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm" style={{ color: PAPER.muted }}>
            No cycle ran in this period.
          </p>
        )}
      </Section>

      <Section copy={copy} title="Still failing" note={report.still_failing.length ? `${report.still_failing.length} shown` : undefined}>
        <TestList items={report.still_failing} empty="Nothing is failing in the latest runs." />
      </Section>

      <div className="grid gap-x-8 md:grid-cols-2">
        <Section copy={copy} title="Turned flaky">
          <TestList items={report.newly_flaky} empty="No test turned flaky." />
        </Section>
        <Section copy={copy} title="Fixed">
          <TestList items={report.fixed} empty="No test was fixed this period." />
        </Section>
      </div>

      {report.slower.length ? (
        <Section copy={copy} title="Got slower">
          <TestList items={report.slower} empty="" />
        </Section>
      ) : null}

      {report.findings.length ? (
        <Section copy={copy} title="What to fix first">
          <ol className="list-decimal space-y-1.5 pl-5 text-sm">
            {report.findings.map((f) => (
              <li key={`${f.cycle}:${f.title}:${f.detail}`}>
                <span className="font-semibold">{f.title}</span>
                <span style={{ color: PAPER.muted }}>
                  {" "}
                  · {f.cycle} · {f.detail}
                </span>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      <footer className="mt-10 border-t pt-3 text-[11px]" style={{ borderColor: PAPER.rule, color: PAPER.faint }}>
        Pass rate counts passed tests out of passed, failed and broken ones. Green builds are runs that exited 0. The health score is the same
        0-100 score as the Project health page.
      </footer>
    </article>
  );
}
