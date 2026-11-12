import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import {
  apiClient,
  type ScorecardResponse,
  type ScorecardScanResponse,
  type ScorecardToolItem,
  type ScorecardToolScoreItem
} from "../../lib/api-client";
import { Badge, Button, Card, EmptyState, PageHeader, Spinner, Switch } from "../../components/ui";
import { MuscleShrug } from "../../components/mascot";
import { copyText } from "../../lib/clipboard";
import { formatRunTime } from "../../lib/format";
import { IS_STATIC_BUILD } from "../../lib/static-backend";
import { ScoreRing, TONE_BAR, TONE_TEXT, scoreTone } from "../health/score";
import { ToolLogo } from "./ToolLogo";

const LOCAL_KEY = "qasight.scorecardTools";

function loadLocalChoices(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_KEY) ?? "{}") as Record<string, boolean>;
  } catch {
    return {}; // storage disabled or a bad value
  }
}

function saveLocalChoices(choices: Record<string, boolean>) {
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(choices));
  } catch {
    // Not remembered between visits; the switches still work on this page.
  }
}

// Same rating as the server (qasight/services/scorecard/rating.py): security and dependencies by their
// worst finding (SonarQube's security rating), code quality by weighted issues per 1,000 source lines.
const DENSITY_WEIGHTS: Record<string, number> = { critical: 10, high: 5, medium: 2, low: 0.5, info: 0.1 };
const DENSITY_CURVE: [number, number][] = [
  [0, 100],
  [2, 90],
  [5, 80],
  [10, 70],
  [25, 60],
  [100, 30]
];
const WORST_BANDS: [string, number, number][] = [
  ["critical", 59, 20],
  ["high", 69, 60],
  ["medium", 79, 70],
  ["low", 89, 80]
];
const WORST_FINDING_CATEGORIES = new Set(["security", "supply-chain"]);
const MIN_LINES = 1000;
const halfUp = (value: number) => Math.floor(value + 0.5);

export function findingsScore(category: string, counts: Record<string, number>, sourceLines: number): number {
  if (WORST_FINDING_CATEGORIES.has(category)) {
    for (const [severity, top, bottom] of WORST_BANDS) {
      const n = counts[severity] ?? 0;
      if (n) return halfUp(Math.max(bottom, top - 2 * Math.log2(n)));
    }
    return 100;
  }
  const points = Object.entries(counts).reduce((sum, [severity, n]) => sum + (DENSITY_WEIGHTS[severity] ?? 0) * n, 0);
  const density = (points * 1000) / Math.max(sourceLines, MIN_LINES);
  for (let i = 1; i < DENSITY_CURVE.length; i += 1) {
    const [x0, y0] = DENSITY_CURVE[i - 1];
    const [x1, y1] = DENSITY_CURVE[i];
    if (density <= x1) return halfUp(y0 + ((y1 - y0) * (density - x0)) / (x1 - x0));
  }
  const [lastX, lastY] = DENSITY_CURVE[DENSITY_CURVE.length - 1];
  return Math.max(0, halfUp((lastY * lastX) / density));
}

const isFindingsTool = (tool: ScorecardToolScoreItem) => "findings" in (tool.metrics ?? {});

/**
 * The same roll-up as the server (qasight/services/scorecard/report.py): in a category, the findings of all its
 * on scanners are rated together against the repository's size; tools that measure something else keep their own
 * score and are averaged in. Overall = mean of the categories that have a score.
 */
export function rollUp(
  tools: ScorecardToolScoreItem[],
  isOn: (tool: string) => boolean,
  pick: (t: ScorecardToolScoreItem) => number | null | undefined,
  sourceLines?: number | null
) {
  const byCategory = new Map<string, number[]>();
  const counts = new Map<string, Record<string, number>>();
  for (const tool of tools) {
    const value = pick(tool);
    // Advisory linters repeat the project's own choice on default rules: shown, not scored.
    if (!isOn(tool.tool) || value == null || tool.metrics?.advisory) continue;
    if (sourceLines && isFindingsTool(tool)) {
      const total = counts.get(tool.category) ?? {};
      for (const [severity, n] of Object.entries(tool.counts ?? {})) total[severity] = (total[severity] ?? 0) + n;
      counts.set(tool.category, total);
    } else {
      byCategory.set(tool.category, [...(byCategory.get(tool.category) ?? []), value]);
    }
  }
  for (const [category, total] of counts) {
    byCategory.set(category, [...(byCategory.get(category) ?? []), findingsScore(category, total, sourceLines ?? 0)]);
  }
  const categories = new Map<string, number>();
  for (const [key, values] of byCategory) categories.set(key, halfUp(values.reduce((a, b) => a + b, 0) / values.length));
  const all = [...categories.values()];
  const score = all.length ? halfUp(all.reduce((a, b) => a + b, 0) / all.length) : null;
  return { score, categories };
}

export function gradeFor(score: number | null): string | null {
  if (score == null) return null;
  if (score >= 90) return "A";
  if (score >= 80) return "B";
  if (score >= 70) return "C";
  if (score >= 60) return "D";
  return "F";
}

/** Same cap as the server (report.py capped_grade): under half of the fitting tools ran, the grade is at most C. */
export function cappedGrade(score: number | null, coverage: ScorecardResponse["coverage"]): { grade: string | null; cappedFrom: string | null } {
  const grade = gradeFor(score);
  if (grade == null || !coverage || !coverage.expected || coverage.ran / coverage.expected >= 0.5) return { grade, cappedFrom: null };
  return "AB".includes(grade) ? { grade: "C", cappedFrom: grade } : { grade, cappedFrom: null };
}

function Delta({ score, previous, label }: { score: number | null | undefined; previous: number | null | undefined; label?: string }) {
  if (score == null || previous == null) return null;
  const diff = score - previous;
  const tone = diff > 0 ? "text-success-300" : diff < 0 ? "text-danger-300" : "text-ink-400";
  const text = diff === 0 ? "=" : `${diff > 0 ? "▲" : "▼"} ${Math.abs(diff)}`;
  return (
    <span className={`text-xs font-semibold ${tone}`} title={label ? `${label}: ${previous}` : `Was ${previous}`}>
      {text}
    </span>
  );
}

const SEVERITY_TONE: Record<string, "danger" | "warn" | "neutral"> = { critical: "danger", high: "danger", medium: "warn", low: "neutral", info: "neutral" };

function toolStatus(tool: ScorecardToolItem, engineRan: boolean): string | null {
  if (tool.kind === "import") return "Import its report";
  if (tool.kind === "engine" && tool.installed === false) return "Needs Docker or Podman on this machine";
  if (tool.in_engine && engineRan) return "MegaLinter ran it for this version";
  if (tool.in_engine) return "Runs on its own when MegaLinter can't";
  if (tool.installed === false) return "Not installed here";
  if (tool.kind === "app") return "Needs a running app";
  return null;
}

function ToolTile({
  tool,
  result,
  on,
  previousVersion,
  engineRan = false,
  onToggle
}: {
  tool: ScorecardToolItem;
  result?: ScorecardToolScoreItem;
  on: boolean;
  previousVersion?: string | null;
  engineRan?: boolean;
  onToggle: (on: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const status = toolStatus(tool, engineRan);
  const fresh = Object.entries(result?.new_counts ?? {}).reduce((sum, [, n]) => sum + n, 0);
  const tone = scoreTone(result?.score);
  return (
    <li
      className={`rounded-2xl border p-3 transition-colors ${on ? "border-brand-600/40 bg-ink-900/70" : "border-white/5 bg-ink-950/50 opacity-80"}`}
      data-testid={`scorecard-tool-${tool.id}`}
    >
      <div className="flex items-start gap-3">
        <ToolLogo tool={tool.id} name={tool.name} />
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen(!open)}
          className="min-w-0 flex-1 text-left"
        >
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className={`text-sm font-semibold ${on ? "text-white" : "text-ink-300"}`}>{tool.name}</span>
            {result ? (
              <span className="inline-flex items-center gap-1.5">
                <span className={`text-sm font-bold ${TONE_TEXT[tone]}`} aria-label={`Score ${result.score} out of 100`}>
                  {result.score}
                </span>
                <Delta score={result.score} previous={result.previous_score} label={previousVersion ?? undefined} />
                {fresh > 0 ? (
                  <Badge tone="warn" title={previousVersion ? `Not found in ${previousVersion}` : undefined}>
                    {fresh} new
                  </Badge>
                ) : null}
              </span>
            ) : null}
          </span>
          <span className="mt-0.5 block text-xs text-ink-400">{result ? result.summary : tool.tagline}</span>
          {!result && status ? <span className="mt-1 block text-[11px] text-ink-500">{status}</span> : null}
        </button>
        <div className="pt-1">
          <Switch on={on} label={`Use ${tool.name}`} onChange={onToggle} />
        </div>
      </div>
      {result ? (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-ink-800" aria-hidden>
          <div className={`h-full rounded-full ${TONE_BAR[tone]}`} style={{ width: `${result.score}%` }} />
        </div>
      ) : null}
      {open ? (
        <div id={panelId} className="mt-3 space-y-2 border-t border-white/5 pt-3 text-xs text-ink-300">
          <p className="text-ink-200">{tool.tagline}</p>
          <p>
            <span className="font-semibold text-ink-200">Score: </span>
            {tool.how}
          </p>
          {tool.report ? (
            <p>
              <span className="font-semibold text-ink-200">Reads: </span>
              {tool.report}
            </p>
          ) : null}
          {tool.install ? (
            <p>
              <span className="font-semibold text-ink-200">Install: </span>
              <code className="font-mono">{tool.install}</code>
            </p>
          ) : null}
          <p>
            <a href={tool.homepage} target="_blank" rel="noreferrer" className="text-brand-300 hover:underline">
              {tool.homepage.replace(/^https?:\/\//, "")}
            </a>
          </p>
          {result && result.findings.length > 0 ? (
            <div className="rounded-xl border border-white/5 bg-ink-950/60 p-2">
              <h4 className="mb-1 font-semibold uppercase tracking-wide text-ink-400">Findings</h4>
              <ul className="space-y-1">
                {result.findings.slice(0, 12).map((finding, index) => (
                  <li key={`${finding.title}:${index}`} className="break-words">
                    <Badge tone={SEVERITY_TONE[finding.severity] ?? "neutral"} className="mr-1.5">
                      {finding.severity}
                    </Badge>
                    <span className="font-semibold text-ink-100">{finding.title}</span>
                    {finding.location ? <span className="font-mono text-ink-400"> {finding.location}</span> : null}
                    {finding.detail ? <span> {finding.detail}</span> : null}
                  </li>
                ))}
              </ul>
              {result.findings.length > 12 ? <p className="mt-1 text-ink-500">and {result.findings.length - 12} more in the Markdown summary.</p> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function ScoreSummary({ card, isOn, allOn }: { card: ScorecardResponse; isOn: (tool: string) => boolean; allOn: boolean }) {
  const now = rollUp(card.tools, isOn, (t) => t.score, card.source_lines);
  // With every tool as the server scored it, its numbers for the version before are exact; after a switch,
  // the version before is re-estimated from each tool's own score.
  const estimate = rollUp(card.tools, isOn, (t) => t.previous_score);
  const before = allOn
    ? {
        score: card.previous_score ?? null,
        categories: new Map(card.categories.filter((c) => c.previous_score != null).map((c) => [c.key, c.previous_score as number]))
      }
    : estimate;
  const graded = cappedGrade(now.score, card.coverage);
  const [copied, setCopied] = useState<string | null>(null);
  const copyMarkdown = async () => {
    try {
      const { markdown } = await apiClient.getScorecardMarkdown(card.project ?? undefined, card.version ?? undefined);
      setCopied((await copyText(markdown)) ? "Copied" : "Copy failed");
    } catch {
      setCopied("Not in this snapshot");
    }
    window.setTimeout(() => setCopied(null), 2500);
  };
  return (
    <Card
      title={`${card.project} ${card.version}`}
      actions={
        <Button size="sm" variant="ghost" onClick={() => void copyMarkdown()}>
          {copied ?? "Copy as Markdown"}
        </Button>
      }
      data-testid="scorecard-score"
    >
      <div className="flex flex-col gap-6 md:flex-row md:items-start">
        <div className="flex flex-col items-center gap-2 md:w-48">
          <ScoreRing score={now.score} grade={graded.grade} label="Release score" />
          {card.coverage ? (
            <p className="text-center text-xs text-ink-300" data-testid="scorecard-coverage">
              {card.coverage.ran} of {card.coverage.expected} tools that fit this repository ran.
              {graded.cappedFrom ? ` Grade capped at C (the score alone is a ${graded.cappedFrom}).` : ""}
            </p>
          ) : null}
          {card.previous_version ? (
            <p className="flex items-center gap-1.5 text-center text-xs text-ink-300">
              vs {card.previous_version}: {before.score ?? "–"} <Delta score={now.score} previous={before.score} />
            </p>
          ) : (
            <p className="text-center text-xs text-ink-400">First scored version of this project.</p>
          )}
          <p className="text-center text-[11px] text-ink-400">
            {card.recorded_at ? `Scored ${formatRunTime(card.recorded_at)}` : ""}
            {card.commit_sha ? ` · ${card.commit_sha.slice(0, 7)}` : ""}
          </p>
        </div>
        <ul className="min-w-0 flex-1 space-y-3">
          {card.categories.map((category) => {
            const score = now.categories.get(category.key) ?? null;
            const tone = scoreTone(score);
            const names = card.tools.filter((t) => category.tools.includes(t.tool));
            return (
              <li key={category.key}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className="text-sm font-semibold text-ink-100">{category.name}</span>
                  <span className="flex items-baseline gap-2">
                    <Delta score={score} previous={before.categories.get(category.key)} label={card.previous_version ?? undefined} />
                    <span className={`text-sm font-semibold ${TONE_TEXT[tone]}`}>{score ?? "Off"}</span>
                  </span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-800" aria-hidden>
                  <div className={`h-full rounded-full ${TONE_BAR[tone]}`} style={{ width: `${score ?? 0}%` }} />
                </div>
                <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-400">
                  {names.map((t) => (
                    <span key={t.tool} className={`inline-flex items-center gap-1 ${isOn(t.tool) ? "" : "line-through opacity-60"}`}>
                      <ToolLogo tool={t.tool} name={t.name} size={16} />
                      {t.name}
                    </span>
                  ))}
                </p>
              </li>
            );
          })}
        </ul>
      </div>
    </Card>
  );
}

function Versions({ card, onPick }: { card: ScorecardResponse; onPick: (version: string) => void }) {
  if (card.versions.length < 2) return null;
  return (
    <Card title="Versions" data-testid="scorecard-versions">
      <ul className="flex items-end gap-2 overflow-x-auto pb-1">
        {[...card.versions].reverse().map((v) => {
          const tone = scoreTone(v.score);
          const current = v.version === card.version;
          return (
            <li key={v.version} className="flex min-w-[4.5rem] flex-col items-center gap-1">
              <span className={`text-xs font-semibold ${TONE_TEXT[tone]}`}>{v.score ?? "–"}</span>
              <div className="flex h-24 w-8 items-end overflow-hidden rounded-lg bg-ink-800" aria-hidden>
                <div className={`w-full ${TONE_BAR[tone]} ${current ? "" : "opacity-60"}`} style={{ height: `${v.score ?? 0}%` }} />
              </div>
              <button
                type="button"
                onClick={() => onPick(v.version)}
                className={`rounded-lg px-1.5 py-0.5 font-mono text-xs ${current ? "bg-brand-600/20 text-brand-200" : "text-ink-300 hover:text-brand-300"}`}
                aria-current={current ? "true" : undefined}
              >
                {v.version}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-xs text-ink-400">Overall score of each scored version, oldest on the left, with every tool switched on.</p>
    </Card>
  );
}

const SCAN_STATUS_TONE: Record<string, "neutral" | "brand" | "success" | "danger" | "warn"> = {
  queued: "neutral",
  running: "brand",
  done: "success",
  error: "danger"
};

function ScanCard({ tools, onDone }: { tools: ScorecardToolItem[]; onDone: (project: string, version: string) => void }) {
  const [target, setTarget] = useState("");
  const [url, setUrl] = useState("");
  const [scanId, setScanId] = useState<string | null>(null);
  const names = new Map(tools.map((t) => [t.id, t.name]));
  const start = useMutation({
    mutationFn: () => apiClient.startScorecardScan({ target: target.trim(), url: url.trim() || null }),
    onSuccess: (data) => setScanId(data.id)
  });
  const scan = useQuery({
    queryKey: ["scorecard-scan", scanId],
    queryFn: () => apiClient.getScorecardScan(scanId ?? ""),
    enabled: Boolean(scanId),
    refetchInterval: (query) => ((query.state.data as ScorecardScanResponse | undefined)?.state === "running" ? 1500 : false)
  });
  const state = scan.data;
  useEffect(() => {
    if (state && state.state === "done" && state.project && state.version) onDone(state.project, state.version);
    // onDone changes identity each render; the scan finishing is what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.state, state?.project, state?.version]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (target.trim()) start.mutate();
  };
  const running = state?.state === "running" || start.isPending;
  const failure = start.error instanceof Error ? start.error.message : state?.state === "failed" ? state.message : null;

  return (
    <Card title="Scan a repository" data-testid="scorecard-scan">
      <p className="text-sm text-ink-300">
        Any repository: a git URL (cloned shallow, like <code className="font-mono text-xs">https://github.com/torvalds/linux</code>) or a folder on the machine running
        QASight. The switched-on tools that fit its languages run here; give an app URL to add ZAP, Lighthouse, axe and k6.
      </p>
      {IS_STATIC_BUILD ? (
        <p className="mt-3 rounded-xl border border-white/10 bg-ink-800/60 px-3 py-2 text-sm text-ink-200" data-testid="scorecard-scan-demo-note">
          This demo is read-only, so it can't scan. Run it on your machine:{" "}
          <code className="font-mono text-xs">qasight scorecard scan https://github.com/OWNER/REPO</code>, then{" "}
          <code className="font-mono text-xs">qasight ui</code>.
        </p>
      ) : null}
      <form onSubmit={submit} className="mt-3 grid gap-2 sm:grid-cols-[2fr_1.3fr_auto]">
        <label className="flex flex-col gap-1 text-xs text-ink-400">
          Repository
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="https://github.com/owner/repo or /path/to/repo"
            className="rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-ink-100"
            disabled={IS_STATIC_BUILD || running}
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-400">
          App URL (optional)
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://localhost:8000"
            className="rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-ink-100"
            disabled={IS_STATIC_BUILD || running}
          />
        </label>
        <div className="flex items-end">
          <Button type="submit" disabled={IS_STATIC_BUILD || running || !target.trim()}>
            {running ? <Spinner /> : null}
            {running ? "Scanning…" : "Scan"}
          </Button>
        </div>
      </form>
      {failure ? (
        <p role="alert" className="mt-3 text-sm text-danger-300">
          {failure}
        </p>
      ) : null}
      {state ? (
        <div className="mt-4 space-y-2" aria-live="polite">
          <p className="text-sm text-ink-200">
            {state.project ? <span className="font-semibold">{`${state.project} ${state.version ?? ""} · `}</span> : null}
            {state.message}
            {state.languages.length ? <span className="text-ink-400"> · {state.languages.join(", ")}</span> : null}
          </p>
          <ul className="grid gap-2 sm:grid-cols-2">
            {state.tools.map((item) => (
              <li key={item.tool} className="flex items-center gap-2 rounded-xl border border-white/5 bg-ink-950/60 px-2 py-1.5 text-xs">
                <ToolLogo tool={item.tool} name={names.get(item.tool) ?? item.tool} size={22} />
                <span className="font-semibold text-ink-100">{names.get(item.tool) ?? item.tool}</span>
                <Badge tone={SCAN_STATUS_TONE[item.status] ?? "neutral"}>{item.status === "done" && item.score != null ? item.score : item.status}</Badge>
                <span className="min-w-0 truncate text-ink-400" title={item.message}>
                  {item.message}
                </span>
              </li>
            ))}
          </ul>
          {Object.keys(state.skipped).length ? (
            <details className="text-xs text-ink-400">
              <summary className="cursor-pointer">Not run ({Object.keys(state.skipped).length})</summary>
              <ul className="mt-1 space-y-0.5">
                {Object.entries(state.skipped).map(([tool, why]) => (
                  <li key={tool}>
                    <span className="text-ink-200">{names.get(tool) ?? tool}</span>: {why}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

function Pickers({
  project,
  version,
  projects,
  versions,
  onChange
}: {
  project: string;
  version: string;
  projects: string[];
  versions: string[];
  onChange: (project: string, version: string) => void;
}) {
  if (projects.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      <label className="flex items-center gap-2 text-sm text-ink-300">
        Project
        <select value={project} onChange={(e) => onChange(e.target.value, "")} className="rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-ink-100">
          {projects.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm text-ink-300">
        Version
        <select value={version} onChange={(e) => onChange(project, e.target.value)} className="rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-ink-100">
          {versions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

/**
 * The release scorecard: the repo review tools (each with its logo and an on/off switch),
 * a version's 0-100 score by category compared with the version before, and a form to
 * scan any repository. Scores come from the tools' own reports, no AI.
 */
export function ScorecardPage() {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const project = params.get("project") ?? "";
  const version = params.get("version") ?? "";
  const projects = useQuery({ queryKey: ["scorecard-projects"], queryFn: () => apiClient.listScorecardProjects() });
  const tools = useQuery({ queryKey: ["scorecard-tools"], queryFn: () => apiClient.listScorecardTools() });
  const card = useQuery({ queryKey: ["scorecard", project, version], queryFn: () => apiClient.getScorecard(project || undefined, version || undefined) });
  const [local, setLocal] = useState<Record<string, boolean>>(IS_STATIC_BUILD ? loadLocalChoices : {});

  const toggle = useMutation({
    mutationFn: ({ tool, on }: { tool: string; on: boolean }) => apiClient.setScorecardTool(tool, on),
    onMutate: ({ tool, on }) => setLocal((prev) => ({ ...prev, [tool]: on })),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["scorecard-tools"] });
      void queryClient.invalidateQueries({ queryKey: ["scorecard-projects"] });
    }
  });

  const items = useMemo(() => tools.data?.items ?? [], [tools.data]);
  const data = card.data?.status === "available" ? card.data : null;
  const results = new Map((data?.tools ?? []).map((t) => [t.tool, t]));
  const isOn = (tool: string) => local[tool] ?? items.find((t) => t.id === tool)?.enabled ?? results.get(tool)?.enabled ?? true;
  const setOn = (tool: string, on: boolean) => {
    if (IS_STATIC_BUILD) {
      const next = { ...local, [tool]: on };
      setLocal(next);
      saveLocalChoices(next);
    } else {
      toggle.mutate({ tool, on });
    }
  };

  const categories = [...new Set(items.map((t) => t.category))];
  const engineName = items.find((t) => t.kind === "engine")?.name ?? "MegaLinter";
  const engineHow = items.find((t) => t.kind === "engine")?.how ?? "";
  const categoryNames = new Map(items.map((t) => [t.category, t.category_name]));
  // The linters MegaLinter ran for this version: not a fixed list, they depend on the repository.
  const ranByEngine: ScorecardToolItem[] = (data?.tools ?? [])
    .filter((t) => t.tool.startsWith("megalinter:"))
    .map((t) => ({
      id: t.tool,
      name: t.name,
      category: t.category,
      category_name: categoryNames.get(t.category) ?? t.category,
      tagline: `${categoryNames.get(t.category) ?? t.category}, run by ${engineName}`,
      how: engineHow,
      homepage: `https://megalinter.io/latest/descriptors/${t.tool.slice("megalinter:".length).toLowerCase()}/`,
      kind: "linter",
      report: "",
      languages: [],
      default_on: true,
      enabled: t.enabled,
      installed: null,
      install: "",
      in_engine: false
    }));
  const engineRan = ranByEngine.length > 0;
  const allTiles = [...items, ...ranByEngine];
  const onCount = allTiles.filter((t) => isOn(t.id)).length;
  const allOn = Object.keys(local).every((tool) => local[tool] === (items.find((t) => t.id === tool)?.enabled ?? results.get(tool)?.enabled ?? true));

  const pick = (nextProject: string, nextVersion: string) => {
    const next: Record<string, string> = {};
    if (nextProject) next.project = nextProject;
    if (nextVersion) next.version = nextVersion;
    setParams(next);
  };

  return (
    <section className="space-y-6">
      <PageHeader
        title="Release scorecard"
        subtitle="MegaLinter picks the linters and scanners that fit any repository; with the API, web, load and test tools they score a version 0-100 and compare it with the version before."
        actions={
          <Pickers
            project={data?.project ?? project}
            version={data?.version ?? version}
            projects={(projects.data?.items ?? []).map((p) => p.project)}
            versions={(data?.versions ?? []).map((v) => v.version)}
            onChange={pick}
          />
        }
      />

      {card.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-ink-300">
          <Spinner /> Loading the scorecard…
        </div>
      ) : card.isError ? (
        <EmptyState mascot={<MuscleShrug />} title="Couldn't load the scorecard" message={card.error instanceof Error ? card.error.message : undefined} />
      ) : data ? (
        <>
          <ScoreSummary card={data} isOn={isOn} allOn={allOn} />
          <Versions card={data} onPick={(v) => pick(data.project ?? "", v)} />
        </>
      ) : (
        <EmptyState
          mascot={<MuscleShrug />}
          title="Nothing scored yet"
          message={
            <>
              Scan a repository below, or run <code className="font-mono text-xs">qasight scorecard scan .</code> in one.
            </>
          }
        />
      )}

      <Card
        title="Tools"
        actions={<span className="text-xs text-ink-400">{`${onCount} of ${allTiles.length} on`}</span>}
        data-testid="scorecard-tools"
      >
        <p className="text-sm text-ink-300">
          Switch a tool off and scans skip it and the score leaves it out. Open a tool for how it scores, what it reads and its findings.
          {IS_STATIC_BUILD ? " In this read-only demo the switches only change what this page shows." : ""}
        </p>
        {tools.isLoading ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-ink-300">
            <Spinner /> Loading tools…
          </div>
        ) : (
          <div className="mt-4 space-y-5">
            {categories.map((category) => {
              const inCategory = items.filter((t) => t.category === category);
              return (
                <div key={category}>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-400">{inCategory[0]?.category_name}</h3>
                  <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                    {inCategory.map((tool) => (
                      <ToolTile
                        key={tool.id}
                        tool={tool}
                        result={results.get(tool.id)}
                        on={isOn(tool.id)}
                        previousVersion={data?.previous_version}
                        engineRan={engineRan}
                        onToggle={(on) => setOn(tool.id, on)}
                      />
                    ))}
                  </ul>
                </div>
              );
            })}
            {engineRan ? (
              <div data-testid="scorecard-engine-linters">
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-400">Run by {engineName} for this version</h3>
                <p className="mb-2 text-xs text-ink-400">
                  {engineName} picked these from the repository&apos;s files. Switch one off and the next scan skips it.
                </p>
                <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                  {ranByEngine.map((tool) => (
                    <ToolTile
                      key={tool.id}
                      tool={tool}
                      result={results.get(tool.id)}
                      on={isOn(tool.id)}
                      previousVersion={data?.previous_version}
                      onToggle={(on) => setOn(tool.id, on)}
                    />
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
        {toggle.error instanceof Error ? (
          <p role="alert" className="mt-3 text-sm text-danger-300">
            {toggle.error.message}
          </p>
        ) : null}
      </Card>

      <ScanCard
        tools={items}
        onDone={(p, v) => {
          void queryClient.invalidateQueries({ queryKey: ["scorecard-projects"] });
          void queryClient.invalidateQueries({ queryKey: ["scorecard"] });
          pick(p, v);
        }}
      />
    </section>
  );
}
