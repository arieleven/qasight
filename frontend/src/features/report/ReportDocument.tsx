import { useRef, type ReactNode } from "react";

import { CopyMenu, cardAnchor } from "../../components/ui";
import type { ManagerReport } from "../../lib/api-client";
import { formatSpan } from "../../lib/format";
import { PassRateChart, RunsChart, SuiteTimeChart } from "./charts";
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

/** "was 4.0d" for numbers where lower is better (time to fix, red time, CI minutes). */
function was(now: number | null, before: number | null, text: string): ReactNode {
  if (now === null || before === null) return null;
  const color = Math.abs(now - before) < 1e-9 ? PAPER.muted : now < before ? PAPER.good : PAPER.bad;
  return <span style={{ color }}> · was {text}</span>;
}

function ms(v: number | null | undefined): string {
  if (v === null || v === undefined) return "–";
  const s = v / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
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

      <DeliverySection report={report} copy={copy} />

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

      <DeliveryByCycle report={report} copy={copy} />

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
        0-100 score as the Project health page. Time to fix is how long a failing test stayed red on the default branch before it passed again
        (flaky tests and reruns of the same commit left out). Red time runs from a red run to the next green one.
      </footer>
    </article>
  );
}

/** Time to fix, open failures, default-branch red time and CI time, with suite time per day and failure ages. */
function DeliverySection({ report, copy }: { report: ManagerReport; copy: boolean }) {
  const d = report.delivery;
  const weekPlus = d.open_by_age.slice(2).reduce((n, b) => n + b.count, 0);
  const branch = d.default_branch ?? "Default branch";
  const most = Math.max(1, ...d.open_by_age.map((b) => b.count));
  return (
    <>
      <Section copy={copy} title="Speed and stability" note="lower is better">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Kpi
            label="Time to fix"
            value={formatSpan(d.median_fix_s)}
            sub={
              d.fixes ? (
                <>
                  median of {d.fixes} {d.fixes === 1 ? "fix" : "fixes"}
                  {was(d.median_fix_s, d.prev_median_fix_s, formatSpan(d.prev_median_fix_s))}
                </>
              ) : (
                "No failing test was fixed"
              )
            }
          />
          <Kpi
            label="Open failures"
            value={String(d.open_failures)}
            accent={d.open_failures ? PAPER.bad : PAPER.good}
            sub={d.open_failures ? `${weekPlus} older than a week` : "Nothing failing"}
          />
          <Kpi
            label={`${branch} red`}
            value={d.red_pct === null ? "–" : `${Math.round(d.red_pct)}%`}
            sub={
              d.red_pct === null ? (
                "No runs on it"
              ) : (
                <>
                  {d.longest_red_s ? `longest spell ${formatSpan(d.longest_red_s)}` : "of the time"}
                  {was(d.red_pct, d.prev_red_pct, d.prev_red_pct === null ? "" : `${Math.round(d.prev_red_pct)}%`)}
                </>
              )
            }
          />
          <Kpi
            label="CI time"
            value={`${Math.round(d.ci_minutes)} min`}
            sub={
              <>
                all test runs together
                {was(d.ci_minutes, d.prev_ci_minutes || null, `${Math.round(d.prev_ci_minutes)} min`)}
              </>
            }
          />
        </div>
        {d.first_try_green_pct !== null ? (
          <p className="mt-2 text-xs" style={{ color: PAPER.muted }}>
            {Math.round(d.first_try_green_pct)}% of {d.first_try_commits} pull request commits passed on their first run.
          </p>
        ) : null}
      </Section>
      <div className="grid gap-x-8 md:grid-cols-2">
        <Section copy={copy} title="Suite time by day" note="median run">
          <SuiteTimeChart points={report.trend} />
        </Section>
        <Section copy={copy} title="Open failures by age">
          <ul className="space-y-2 text-sm" aria-label="Open failures by how long they have been failing">
            {d.open_by_age.map((b, i) => (
              <li key={b.label} className="grid grid-cols-[7rem_1fr_2rem] items-center gap-2">
                <span style={{ color: PAPER.muted }}>{b.label}</span>
                <span className="h-3 rounded" style={{ background: PAPER.wash }}>
                  <span
                    className="block h-3 rounded"
                    style={{ width: `${(100 * b.count) / most}%`, background: i >= 2 ? PAPER.bad : PAPER.warn }}
                  />
                </span>
                <span className="text-right tabular-nums">{b.count}</span>
              </li>
            ))}
          </ul>
          {d.oldest_open.length ? (
            <table className="mt-4 w-full text-sm">
              <thead>
                <tr className="text-left text-xs" style={{ color: PAPER.muted }}>
                  <th className="py-1 pr-3 font-semibold">Oldest</th>
                  <th className="py-1 pr-3 font-semibold">Cycle</th>
                  <th className="py-1 text-right font-semibold">Red for</th>
                </tr>
              </thead>
              <tbody>
                {d.oldest_open.slice(0, 5).map((f) => (
                  <tr key={`${f.cycle}:${f.name}`} className="border-t align-top" style={{ borderColor: PAPER.rule }}>
                    <td className="py-1.5 pr-3 font-mono text-[12px] break-all">{f.name}</td>
                    <td className="py-1.5 pr-3">{f.cycle}</td>
                    <td className="py-1.5 text-right tabular-nums">{formatSpan(f.age_s)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </Section>
      </div>
    </>
  );
}

/** One row per cycle: time to fix, suite time, CI minutes, red time and the stage that grew. */
function DeliveryByCycle({ report, copy }: { report: ManagerReport; copy: boolean }) {
  const rows = report.delivery.cycles;
  if (!rows.length) return null;
  const grown = rows.some((c) => c.grown_stage);
  return (
    <Section copy={copy} title="Speed and stability by cycle">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] text-sm">
          <thead>
            <tr className="text-left text-xs" style={{ color: PAPER.muted }}>
              <th className="py-1 pr-3 font-semibold">Cycle</th>
              <th className="py-1 pr-3 text-right font-semibold">Time to fix</th>
              <th className="py-1 pr-3 text-right font-semibold">Open</th>
              <th className="py-1 pr-3 text-right font-semibold">Suite time</th>
              <th className="py-1 pr-3 text-right font-semibold">Slowest 10%</th>
              <th className="py-1 pr-3 text-right font-semibold">CI min</th>
              <th className="py-1 pr-3 text-right font-semibold">Red</th>
              {grown ? <th className="py-1 font-semibold">Stage that grew</th> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.cycle} className="border-t tabular-nums" style={{ borderColor: PAPER.rule }}>
                <td className="py-1.5 pr-3 font-medium">{c.cycle}</td>
                <td className="py-1.5 pr-3 text-right">{formatSpan(c.median_fix_s)}</td>
                <td className="py-1.5 pr-3 text-right" style={{ color: c.open_failures ? PAPER.bad : undefined }}>
                  {c.open_failures}
                </td>
                <td className="py-1.5 pr-3 text-right">{ms(c.suite_median_ms)}</td>
                <td className="py-1.5 pr-3 text-right">{ms(c.suite_p90_ms)}</td>
                <td className="py-1.5 pr-3 text-right">{Math.round(c.ci_minutes)}</td>
                <td className="py-1.5 pr-3 text-right">{c.red_pct === null ? "–" : `${Math.round(c.red_pct)}%`}</td>
                {grown ? (
                  <td className="py-1.5">
                    {c.grown_stage ? `${c.grown_stage}: ${ms(c.grown_stage_before_ms)} → ${ms(c.grown_stage_after_ms)}` : "–"}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
