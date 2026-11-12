import { useRef, type ReactNode } from "react";

import { CopyMenu, cardAnchor } from "../../components/ui";
import type { ManagerReport } from "../../lib/api-client";
import { formatSpan } from "../../lib/format";
import { DailyPercentChart, PassRateChart, RunsChart, SuiteTimeChart } from "./charts";
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
    <div className="overflow-x-auto" tabIndex={0}>
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
 * fixed colours. The same component is what `qasight export` renders to PDF,
 * PNG and HTML, so the screen, the printout and the files match.
 */
export function ReportDocument({ report, brand = "QASight", copy = true }: { report: ManagerReport; brand?: string; copy?: boolean }) {
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

      <DeliverySection report={report} copy={copy} />
      <SignalSection report={report} copy={copy} />

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
          <div className="overflow-x-auto" tabIndex={0}>
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

      <TeamsSection report={report} copy={copy} />
      <HygieneSection report={report} copy={copy} />
      <PatternsSection report={report} copy={copy} />

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
      <div className="overflow-x-auto" tabIndex={0}>
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

const CAUSE_COLORS: Record<string, string> = {
  product_bug: PAPER.bad,
  test_bug: PAPER.warn,
  environment: PAPER.brand,
  other: PAPER.muted,
  unmarked: PAPER.faint,
  flaky: "#d97706",
  no_tests: "#7c3aed"
};

/** Why the red runs were red (real bugs vs noise) and what flaky tests cost. */
function SignalSection({ report, copy }: { report: ManagerReport; copy: boolean }) {
  const s = report.signal;
  if (!s.red_runs && !s.flaky_minutes) return null;
  const real = s.by_cause.find((c) => c.cause === "product_bug")?.runs ?? 0;
  return (
    <div className="grid gap-x-8 md:grid-cols-2">
      <Section copy={copy} title="Why runs were red">
        {s.red_runs ? (
          <>
            <p className="text-sm" style={{ color: PAPER.ink }}>
              <span className="text-2xl font-bold tabular-nums">{real}</span> of {s.red_runs} red {s.red_runs === 1 ? "run was a real bug" : "runs were real bugs"}
              {s.noise_pct !== null ? (
                <span style={{ color: PAPER.muted }}>
                  {" "}
                  · {Math.round(s.noise_pct)}% noise
                  {s.prev_noise_pct !== null ? `, was ${Math.round(s.prev_noise_pct)}%` : ""}
                </span>
              ) : null}
            </p>
            <div
              className="mt-3 flex h-3 overflow-hidden rounded"
              role="img"
              aria-label={`Red runs by cause: ${s.by_cause.map((c) => `${c.label} ${c.runs}`).join(", ")}`}
              style={{ background: PAPER.wash }}
            >
              {s.by_cause.map((c) => (
                <span key={c.cause} style={{ width: `${(100 * c.runs) / s.red_runs}%`, background: CAUSE_COLORS[c.cause] }} />
              ))}
            </div>
            <ul className="mt-3 space-y-1 text-sm">
              {s.by_cause.map((c) => (
                <li key={c.cause} className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CAUSE_COLORS[c.cause] }} />
                    {c.label}
                  </span>
                  <span className="tabular-nums">{c.runs}</span>
                </li>
              ))}
            </ul>
            {s.unmarked_pct ? (
              <p className="mt-2 text-xs" style={{ color: PAPER.muted }}>
                Mark a failure's cause on its run page and later failures like it are counted too.
              </p>
            ) : null}
          </>
        ) : (
          <p className="text-sm" style={{ color: PAPER.muted }}>
            No red runs this period.
          </p>
        )}
      </Section>
      <Section copy={copy} title="Cost of flakiness">
        <p className="text-sm" style={{ color: PAPER.ink }}>
          <span className="text-2xl font-bold tabular-nums">{Math.round(s.flaky_minutes)}</span> CI minutes
          {was(s.flaky_minutes, s.prev_flaky_minutes || null, `${Math.round(s.prev_flaky_minutes)}`)}
          <span style={{ color: PAPER.muted }}>
            {" "}
            · {s.flaky_red_runs} red {s.flaky_red_runs === 1 ? "run" : "runs"}
            {s.flaky_branches ? `, ${s.flaky_branches} ${s.flaky_branches === 1 ? "branch" : "branches"} held up` : ""}
          </span>
        </p>
        {s.costly.length ? (
          <table className="mt-3 w-full text-sm">
            <thead>
              <tr className="text-left text-xs" style={{ color: PAPER.muted }}>
                <th className="py-1 pr-3 font-semibold">Most costly</th>
                <th className="py-1 pr-3 text-right font-semibold">Red runs</th>
                <th className="py-1 text-right font-semibold">Minutes</th>
              </tr>
            </thead>
            <tbody>
              {s.costly.slice(0, 5).map((t) => (
                <tr key={`${t.cycle}:${t.key}`} className="border-t align-top" style={{ borderColor: PAPER.rule }}>
                  <td className="py-1.5 pr-3">
                    <span className="font-mono text-[12px] break-all">{t.name}</span>
                    <span style={{ color: PAPER.muted }}> · {t.cycle}</span>
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{t.red_runs}</td>
                  <td className="py-1.5 text-right tabular-nums">{t.cost_minutes.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="mt-2 text-sm" style={{ color: PAPER.muted }}>
            No flaky test cost CI time this period.
          </p>
        )}
      </Section>
    </div>
  );
}

/** Each owner's numbers, so every team has its own line instead of one shared score. */
function TeamsSection({ report, copy }: { report: ManagerReport; copy: boolean }) {
  const t = report.teams;
  if (!t.rows.length) return null;
  return (
    <Section copy={copy} title="By team" note={t.owners_known ? "CODEOWNERS and owner labels" : undefined}>
      {t.owners_known ? (
        <div className="overflow-x-auto" tabIndex={0}>
          <table className="w-full min-w-[560px] text-sm">
            <thead>
              <tr className="text-left text-xs" style={{ color: PAPER.muted }}>
                <th className="py-1 pr-3 font-semibold">Owner</th>
                <th className="py-1 pr-3 text-right font-semibold">Tests</th>
                <th className="py-1 pr-3 text-right font-semibold">Failing</th>
                <th className="py-1 pr-3 text-right font-semibold">Open, oldest</th>
                <th className="py-1 pr-3 text-right font-semibold">Time to fix</th>
                <th className="py-1 pr-3 text-right font-semibold">Flaky</th>
                <th className="py-1 text-right font-semibold">Flaky cost</th>
              </tr>
            </thead>
            <tbody>
              {t.rows.map((r) => (
                <tr key={r.owner} className="border-t tabular-nums" style={{ borderColor: PAPER.rule }}>
                  <td className="py-1.5 pr-3 font-medium break-all">{r.owner}</td>
                  <td className="py-1.5 pr-3 text-right">{r.tests}</td>
                  <td className="py-1.5 pr-3 text-right" style={{ color: r.failing ? PAPER.bad : undefined }}>
                    {r.failing}
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    {r.open_failures ? `${r.open_failures}, ${formatSpan(r.oldest_open_s)}` : "0"}
                  </td>
                  <td className="py-1.5 pr-3 text-right">{formatSpan(r.median_fix_s)}</td>
                  <td className="py-1.5 pr-3 text-right" style={{ color: r.flaky ? PAPER.warn : undefined }}>
                    {r.flaky}
                  </td>
                  <td className="py-1.5 text-right">{r.flaky_minutes ? `${r.flaky_minutes.toFixed(1)} min` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm" style={{ color: PAPER.muted }}>
          No test has an owner yet. Add a CODEOWNERS file to the repository, or an owner label to tests (an Allure owner label, a pytest
          marker or a JUnit property), and this section splits the numbers by team.
        </p>
      )}
    </Section>
  );
}

function NameList({ items, empty }: { items: ManagerReport["hygiene"]["added_tests"]; empty: string }) {
  if (!items.length)
    return (
      <p className="text-sm" style={{ color: PAPER.muted }}>
        {empty}
      </p>
    );
  return (
    <ul className="space-y-1 text-sm">
      {items.map((t) => (
        <li key={`${t.cycle}:${t.name}`}>
          <span className="font-mono text-[12px] break-all">{t.name}</span>
          <span style={{ color: PAPER.muted }}>
            {" "}
            · {t.cycle}
            {t.detail ? ` · ${t.detail}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Is the suite growing with the code, and what costs time or checks nothing? */
function HygieneSection({ report, copy }: { report: ManagerReport; copy: boolean }) {
  const h = report.hygiene;
  if (!h.tests) return null;
  const delta = h.prev_tests === null ? null : h.tests - h.prev_tests;
  return (
    <>
      <Section copy={copy} title="Suite size">
        <p className="text-sm" style={{ color: PAPER.ink }}>
          <span className="text-2xl font-bold tabular-nums">{h.tests.toLocaleString()}</span> tests
          {delta !== null ? (
            <span style={{ color: PAPER.muted }}>
              {" "}
              · {delta >= 0 ? "+" : "−"}
              {Math.abs(delta)} since the period before ({h.added} added, {h.removed} removed)
            </span>
          ) : null}
        </p>
        {h.added_tests.length || h.removed_tests.length ? (
          <div className="mt-3 grid gap-x-8 md:grid-cols-2">
            <div>
              <p className="mb-1 text-xs font-semibold" style={{ color: PAPER.muted }}>
                Added
              </p>
              <NameList items={h.added_tests} empty="None." />
            </div>
            <div>
              <p className="mb-1 text-xs font-semibold" style={{ color: PAPER.muted }}>
                Removed
              </p>
              <NameList items={h.removed_tests} empty="None." />
            </div>
          </div>
        ) : null}
      </Section>
      <div className="grid gap-x-8 md:grid-cols-2">
        <Section copy={copy} title="Skipped in every run" note={h.always_skipped_count ? String(h.always_skipped_count) : undefined}>
          <NameList items={h.always_skipped} empty="No test was skipped every time." />
        </Section>
        <Section copy={copy} title="Slow and never failed" note={h.slow_never_failed.length ? `${ms(h.slow_never_failed_ms)} a run` : undefined}>
          <NameList items={h.slow_never_failed} empty="No slow test passed every run." />
          {h.slow_never_failed.length ? (
            <p className="mt-2 text-xs" style={{ color: PAPER.muted }}>
              The slowest tenth of the suite, and they passed every run: the first place to look when the suite is too slow (a nightly
              cycle, or a faster test that covers the same thing).
            </p>
          ) : null}
        </Section>
      </div>
    </>
  );
}

function CountedList({ items, empty, unit }: { items: ManagerReport["patterns"]["top_tests"]; empty: string; unit?: string }) {
  if (!items.length)
    return (
      <p className="text-sm" style={{ color: PAPER.muted }}>
        {empty}
      </p>
    );
  return (
    <ul className="space-y-1 text-sm">
      {items.map((t) => (
        <li key={`${t.cycle}:${t.name}`} className="flex items-baseline justify-between gap-3">
          <span className="min-w-0">
            <span className="font-mono text-[12px] break-all">{t.name}</span>
            <span style={{ color: PAPER.muted }}>
              {" "}
              · {t.cycle}
              {t.detail ? ` · ${t.detail}` : ""}
            </span>
          </span>
          {unit ? (
            <span className="shrink-0 tabular-nums">
              {t.count} {unit}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** Where failures concentrate, when they happen, which tests catch bugs, retries and coverage over time. */
function PatternsSection({ report, copy }: { report: ManagerReport; copy: boolean }) {
  const p = report.patterns;
  const hasCoverage = p.days.some((d) => d.coverage_pct != null);
  const worstDay = [...p.by_weekday].sort((a, b) => b.red / Math.max(1, b.runs) - a.red / Math.max(1, a.runs))[0];
  return (
    <>
      {p.failures ? (
        <div className="grid gap-x-8 md:grid-cols-2">
          <Section copy={copy} title="Where failures concentrate">
            {p.top_share_pct !== null && p.top_tests.length ? (
              <p className="mb-2 text-sm" style={{ color: PAPER.ink }}>
                {p.top_tests.length} {p.top_tests.length === 1 ? "test" : "tests"} caused {Math.round(p.top_share_pct)}% of the {p.failures} test failures.
              </p>
            ) : null}
            <CountedList items={p.top_tests} empty="" unit="failures" />
          </Section>
          <Section copy={copy} title="Most repeated errors">
            {p.repeated_errors.length ? (
              <ul className="space-y-2 text-sm">
                {p.repeated_errors.slice(0, 5).map((e) => (
                  <li key={e.message}>
                    <span className="block font-mono text-[12px] break-all">{e.message}</span>
                    <span className="text-xs" style={{ color: PAPER.muted }}>
                      {e.count} failures in {e.tests} {e.tests === 1 ? "test" : "tests"}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm" style={{ color: PAPER.muted }}>
                No error message repeated.
              </p>
            )}
          </Section>
        </div>
      ) : null}

      <div className="grid gap-x-8 md:grid-cols-2">
        <Section copy={copy} title="Retry rate by day" note={p.retry_pct !== null ? `${p.retry_pct.toFixed(1)}% this period` : undefined}>
          <DailyPercentChart points={p.days} field="retry_pct" label="Share of tests that passed only on a retry" />
          <p className="mt-1 text-xs" style={{ color: PAPER.muted }}>
            Tests that passed only after a retry
            {p.prev_retry_pct !== null ? `; ${p.prev_retry_pct.toFixed(1)}% the period before` : ""}. It climbs before tests count as flaky.
          </p>
        </Section>
        {hasCoverage ? (
          <Section copy={copy} title="Line coverage by day">
            <DailyPercentChart points={p.days} field="coverage_pct" label="Line coverage" />
          </Section>
        ) : (
          <Section copy={copy} title="Unstable timing">
            <CountedList items={p.unstable_timing} empty="No test's timing jumps around." />
          </Section>
        )}
      </div>

      {hasCoverage && p.unstable_timing.length ? (
        <Section copy={copy} title="Unstable timing">
          <CountedList items={p.unstable_timing} empty="" />
        </Section>
      ) : null}

      {p.when_findings.length || p.by_environment.length > 1 ? (
        <Section copy={copy} title="When failures happen">
          {p.when_findings.length ? (
            <ul className="mb-3 list-disc space-y-1 pl-5 text-sm">
              {p.when_findings.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          ) : (
            <p className="mb-3 text-sm" style={{ color: PAPER.muted }}>
              No day, hour or environment fails much more often than the rest
              {worstDay ? ` (the worst day is ${worstDay.label}, ${worstDay.red} of ${worstDay.runs} red)` : ""}.
            </p>
          )}
          {p.by_environment.length > 1 ? (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs" style={{ color: PAPER.muted }}>
                  <th className="py-1 pr-3 font-semibold">Environment</th>
                  <th className="py-1 pr-3 text-right font-semibold">Runs</th>
                  <th className="py-1 text-right font-semibold">Red</th>
                </tr>
              </thead>
              <tbody>
                {p.by_environment.map((s) => (
                  <tr key={s.label} className="border-t tabular-nums" style={{ borderColor: PAPER.rule }}>
                    <td className="py-1.5 pr-3 break-all">{s.label}</td>
                    <td className="py-1.5 pr-3 text-right">{s.runs}</td>
                    <td className="py-1.5 text-right">{Math.round((100 * s.red) / Math.max(1, s.runs))}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </Section>
      ) : null}

      {p.caught_bugs.length || p.only_noise.length ? (
        <div className="grid gap-x-8 md:grid-cols-2">
          <Section copy={copy} title="Tests that caught real bugs">
            <CountedList items={p.caught_bugs} empty="No failure was marked as a product bug." unit="bugs" />
          </Section>
          <Section copy={copy} title="Tests that only raised false alarms">
            <CountedList items={p.only_noise} empty="None." unit="alarms" />
          </Section>
        </div>
      ) : null}
    </>
  );
}
