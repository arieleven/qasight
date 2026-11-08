import { useRef, useState, type DragEvent } from "react";

import { Badge, Button, Card, PageHeader, StackedBar } from "../../components/ui";
import { countLabel, formatDuration } from "../../lib/format";
import { JunitParseError, SAMPLE_JUNIT, parseJunit, summarise, type JunitCase, type JunitReport } from "../../lib/junit";

const SHOWN_ROWS = 50;
const FAILED = new Set(["failed", "broken"]);

async function readReports(files: File[]): Promise<{ report: JunitReport; names: string[] }> {
  const cases: JunitCase[] = [];
  for (const file of files) {
    try {
      cases.push(...parseJunit(await file.text()).cases);
    } catch (err) {
      const reason = err instanceof JunitParseError ? err.message : "It couldn't be read.";
      throw new JunitParseError(`${file.name}: ${reason}`);
    }
  }
  return { report: summarise(cases), names: files.map((f) => f.name) };
}

function CaseRow({ test }: { test: JunitCase }) {
  return (
    <li className="space-y-1 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="break-all font-medium text-ink-50">{test.name}</span>
        {test.status === "broken" ? <Badge>Error</Badge> : null}
      </div>
      {test.message ? <p className="break-words font-mono text-xs text-danger-300">{test.message}</p> : null}
      <p className="text-xs text-ink-400">
        {[test.classname || test.suite, test.durationMs != null ? formatDuration(test.durationMs) : null]
          .filter(Boolean)
          .join(" · ")}
      </p>
    </li>
  );
}

function Results({ report, names }: { report: JunitReport; names: string[] }) {
  const { totals } = report;
  const failing = report.cases.filter((c) => FAILED.has(c.status));
  const slowest = [...report.cases]
    .filter((c) => c.durationMs != null)
    .sort((a, b) => (b.durationMs ?? 0) - (a.durationMs ?? 0))
    .slice(0, 5);
  const failed = totals.failed + totals.broken;

  return (
    <>
      <Card title="Result">
        <p className="text-sm text-ink-100">
          {failed > 0
            ? `${countLabel(failed, "test")} of ${totals.total} failed`
            : `All ${countLabel(totals.total - totals.skipped, "test")} passed`}
          {totals.skipped > 0 ? `, ${totals.skipped} skipped` : ""}, {formatDuration(totals.durationMs)} of test time.
        </p>
        <StackedBar
          className="mt-3"
          segments={[
            { label: "Passed", value: totals.passed, colorClass: "bg-success-500" },
            { label: "Failed", value: failed, colorClass: "bg-danger-500" },
            { label: "Skipped", value: totals.skipped, colorClass: "bg-ink-500" }
          ]}
        />
        <p className="mt-2 text-xs text-ink-400">From {names.join(", ")}. Read in your browser; nothing was uploaded.</p>
      </Card>

      {failing.length > 0 ? (
        <Card title={`Failing tests (${failing.length})`}>
          {report.groups.length > 0 ? (
            <div className="mb-4 rounded-2xl border border-warn-500/30 bg-warn-500/5 p-3">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-warn-300">Same cause</p>
              <ul className="space-y-1 text-sm">
                {report.groups.map((group) => (
                  <li key={group.message}>
                    <span className="font-semibold text-ink-50">{countLabel(group.names.length, "test")}:</span>{" "}
                    <span className="font-mono text-xs text-danger-300">{group.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <ul className="divide-fade">
            {failing.slice(0, SHOWN_ROWS).map((test, idx) => (
              // Names can repeat across suites; position keeps rows apart.
              <CaseRow key={`${test.classname}-${test.name}-${idx}`} test={test} />
            ))}
          </ul>
          {failing.length > SHOWN_ROWS ? (
            <p className="mt-2 text-xs text-ink-400">and {failing.length - SHOWN_ROWS} more</p>
          ) : null}
        </Card>
      ) : null}

      {slowest.length > 0 ? (
        <Card title="Slowest tests">
          <ol className="space-y-1.5 text-sm">
            {slowest.map((test, idx) => (
              <li key={`${test.classname}-${test.name}-${idx}`} className="flex items-baseline justify-between gap-3">
                <span className="break-all text-ink-100">{test.name}</span>
                <span className="shrink-0 text-xs text-ink-300">{formatDuration(test.durationMs)}</span>
              </li>
            ))}
          </ol>
        </Card>
      ) : null}

      <Card title="Get this on every pull request">
        <p className="text-sm text-ink-300">
          One file shows one run. In your repository, <code className="font-mono text-xs">qasight init</code> finds
          your test runners, writes the config and a GitHub Actions workflow that comments on each pull request,
          and records a first run:
        </p>
        <pre className="mt-2 overflow-x-auto rounded-xl bg-ink-950/80 p-3 font-mono text-xs text-ink-100" data-testid="try-setup">
          {"pip install 'qasight[api]'\nqasight init --dry-run   # see the files first\nqasight init --pr        # write them and open the pull request\nqasight ui"}
        </pre>
        <p className="mt-2 text-xs text-ink-400">
          No Python? <code className="font-mono">uvx --from qasight qasight init</code>. Only want to record results
          you already have? <code className="font-mono">qasight ingest</code> finds them.
        </p>
      </Card>
    </>
  );
}

/** Drop JUnit XML and see it the way QASight shows a run, entirely in the browser. */
export function TryPage() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ report: JunitReport; names: string[] } | null>(null);

  async function load(files: File[]) {
    if (files.length === 0) return;
    setError(null);
    try {
      setResult(await readReports(files));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void load(Array.from(event.dataTransfer.files));
  }

  return (
    <section className="space-y-4">
      <PageHeader
        title="Try it with your own tests"
        subtitle="Drop a JUnit XML report: pytest --junitxml, Jest, Vitest, go test, Maven Surefire, Playwright's junit reporter. It's read in your browser and never uploaded."
      />

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragging ? "border-brand-400 bg-brand-500/10" : "border-ink-600 bg-ink-900/40"
        }`}
      >
        <p className="text-sm text-ink-100">Drop JUnit XML files here</p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button onClick={() => inputRef.current?.click()}>Choose files</Button>
          <Button
            variant="secondary"
            onClick={() => {
              setError(null);
              setResult({ report: parseJunit(SAMPLE_JUNIT), names: ["sample.xml"] });
            }}
          >
            Use a sample report
          </Button>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".xml,text/xml,application/xml"
          multiple
          className="sr-only"
          aria-label="JUnit XML files"
          onChange={(event) => {
            void load(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
      </div>

      {error ? (
        <p role="alert" className="text-sm text-danger-400">
          {error}
        </p>
      ) : null}
      {result ? <Results report={result.report} names={result.names} /> : null}
    </section>
  );
}
