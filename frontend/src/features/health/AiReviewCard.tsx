import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient, type ReviewSkillItem, type SkillResultItem } from "../../lib/api-client";
import { Badge, Button, Card, Spinner } from "../../components/ui";
import { formatRunTime } from "../../lib/format";
import { IS_STATIC_BUILD } from "../../lib/static-backend";
import { ScoreRing, scoreTone } from "./score";
import { AiNotReadyNote } from "../../components/AiNotReadyNote";

const STORAGE_KEY = "testo.reviewSkills";

function loadChoices(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Record<string, boolean>;
  } catch {
    return {}; // storage disabled or a bad value: fall back to each skill's default
  }
}

function saveChoices(choices: Record<string, boolean>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(choices));
  } catch {
    // Not remembered between visits; the toggles still work.
  }
}

const SEVERITY_TONE = { high: "danger", medium: "warn", low: "neutral" } as const;

function Switch({ on, label, onChange }: { on: boolean; label: string; onChange: (on: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors ${
        on ? "border-brand-500 bg-brand-600" : "border-white/10 bg-ink-800"
      }`}
    >
      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${on ? "translate-x-6" : "translate-x-1"}`} />
    </button>
  );
}

function ResultScore({ result }: { result: SkillResultItem }) {
  const tone = scoreTone(result.score);
  return (
    <Badge tone={tone === "neutral" ? "neutral" : tone} aria-label={result.score == null ? "No score" : `Score ${result.score} out of 100`}>
      {result.score ?? "n/a"}
    </Badge>
  );
}

function SkillRow({
  skill,
  on,
  result,
  onToggle
}: {
  skill: ReviewSkillItem;
  on: boolean;
  result?: SkillResultItem;
  onToggle: (on: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <li className={`rounded-2xl border p-3 transition-colors ${on ? "border-brand-600/40 bg-ink-900/70" : "border-white/5 bg-ink-950/50"}`} data-testid={`skill-${skill.id}`}>
      <div className="flex items-start gap-3">
        <div className="pt-0.5">
          <Switch on={on} label={skill.name} onChange={onToggle} />
        </div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen(!open)}
          className="flex min-w-0 flex-1 items-start justify-between gap-2 text-left"
        >
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <span className={`text-sm font-semibold ${on ? "text-white" : "text-ink-300"}`}>{skill.name}</span>
              <Badge tone="brand">{skill.category}</Badge>
              {result ? <ResultScore result={result} /> : null}
            </span>
            <span className="mt-0.5 block text-xs text-ink-400">{skill.tagline}</span>
          </span>
          <svg viewBox="0 0 24 24" className={`mt-1 h-4 w-4 shrink-0 text-ink-400 transition-transform ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
      </div>
      {open ? (
        <div id={panelId} className="mt-3 space-y-3 border-t border-white/5 pt-3 text-sm sm:pl-14">
          <p className="text-ink-200">{skill.summary}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-400">What it checks</h4>
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-300">
                {skill.checks.map((check) => (
                  <li key={check}>{check}</li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-400">What it reads</h4>
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-300">
                {skill.reads.map((read) => (
                  <li key={read}>{read}</li>
                ))}
              </ul>
            </div>
          </div>
          {result ? (
            <div className="rounded-xl border border-white/5 bg-ink-950/60 p-3">
              <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-400">Last review</h4>
              <p className="text-ink-200">{result.summary}</p>
              {result.findings.length > 0 ? (
                <ul className="mt-2 space-y-1.5">
                  {result.findings.map((finding, index) => (
                    <li key={`${finding.title}:${index}`} className="text-xs">
                      <Badge tone={SEVERITY_TONE[finding.severity]} className="mr-1.5">
                        {finding.severity}
                      </Badge>
                      <span className="font-semibold text-ink-100">{finding.title}</span>
                      <span className="text-ink-300"> {finding.detail}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/**
 * The optional AI review: the checks it can run, each switched on or off and
 * expandable to what it checks and reads, and the last review's scores.
 * Only about the tests: it never reads source code or judges the product.
 */
export function AiReviewCard({ cycle }: { cycle?: string }) {
  const queryClient = useQueryClient();
  const skills = useQuery({ queryKey: ["review-skills"], queryFn: () => apiClient.listReviewSkills() });
  const aiStatus = useQuery({ queryKey: ["ai-config-status"], queryFn: () => apiClient.getAiConfigStatus() });
  const review = useQuery({ queryKey: ["project-review", cycle ?? ""], queryFn: () => apiClient.getProjectReview(cycle) });
  const [choices, setChoices] = useState<Record<string, boolean>>(loadChoices);

  const items = skills.data?.items ?? [];
  const isOn = (skill: ReviewSkillItem) => choices[skill.id] ?? skill.default_on;
  const enabled = items.filter(isOn).map((skill) => skill.id);

  const choose = (next: Record<string, boolean>) => {
    setChoices(next);
    saveChoices(next);
  };

  const run = useMutation({
    mutationFn: () => apiClient.generateProjectReview(cycle, enabled),
    onSuccess: (data) => {
      if (data.status === "available") queryClient.setQueryData(["project-review", cycle ?? ""], data);
    }
  });

  const aiReady = Boolean(aiStatus.data?.enabled && aiStatus.data?.configured);
  const current = review.data?.status === "available" ? review.data : null;
  const results = new Map((current?.skills ?? []).map((result) => [result.skill_id, result]));
  const failure = run.data?.status === "failed" ? run.data.error_message : run.error instanceof Error ? run.error.message : null;

  return (
    <Card
      title="AI review"
      actions={<Badge tone="warn">AI estimate</Badge>}
      data-testid="ai-review"
    >
      <p className="text-sm text-ink-300">
        Your AI provider reads the same results (test names, failure messages and run statistics, never your source code) and scores each check you switch on. It is
        an opinion: asking again can give a different answer, and each review is one call to your provider.
      </p>

      {IS_STATIC_BUILD ? (
        <p className="mt-3 rounded-xl border border-white/10 bg-ink-800/60 px-3 py-2 text-sm text-ink-200" data-testid="ai-review-demo-note">
          This demo is read-only, so the AI review can't run here. Browse the checks below, then run it on your own project with{" "}
          <code className="font-mono text-xs">qasight ui</code> and an AI provider (a free Gemini key works).
        </p>
      ) : aiStatus.data && !aiReady ? (
        <AiNotReadyNote status={aiStatus.data} />
      ) : null}

      {current ? (
        <div className="mt-4 flex flex-col gap-4 rounded-2xl border border-white/5 bg-ink-950/60 p-4 sm:flex-row sm:items-center" data-testid="ai-review-result">
          <ScoreRing score={current.score} grade={current.grade} label="AI review score" size={104} />
          <div className="min-w-0 space-y-1">
            {current.summary ? <p className="text-sm text-ink-100">{current.summary}</p> : null}
            <p className="text-xs text-ink-400">
              Average of {current.skills.length} check{current.skills.length === 1 ? "" : "s"}. {current.model ? `Model ${current.model}` : ""}
              {current.generated_at ? `, ${formatRunTime(current.generated_at)}` : ""}. Open a check below for its findings.
            </p>
            {current.stale ? <p className="text-xs text-warn-300">Newer runs came in since this review. Run it again to include them.</p> : null}
          </div>
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-white">
          Checks <span className="font-normal text-ink-400">· {enabled.length} of {items.length} on</span>
        </h3>
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => choose(Object.fromEntries(items.map((s) => [s.id, true])))}>
            All on
          </Button>
          <Button size="sm" variant="ghost" onClick={() => choose({})}>
            Defaults
          </Button>
        </div>
      </div>

      {skills.isLoading ? (
        <div className="mt-3 flex items-center gap-2 text-sm text-ink-300">
          <Spinner /> Loading checks…
        </div>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((skill) => (
            <SkillRow key={skill.id} skill={skill} on={isOn(skill)} result={results.get(skill.id)} onToggle={(on) => choose({ ...choices, [skill.id]: on })} />
          ))}
        </ul>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={() => run.mutate()} disabled={IS_STATIC_BUILD || run.isPending || enabled.length === 0 || !aiReady}>
          {run.isPending ? <Spinner /> : null}
          {current ? "Run the review again" : "Run AI review"}
        </Button>
        {enabled.length === 0 ? <span className="text-xs text-ink-400">Switch on at least one check.</span> : null}
        {run.isPending ? <span className="text-xs text-ink-400">This can take up to a minute on a free provider.</span> : null}
      </div>
      {failure ? (
        <p role="alert" className="mt-3 text-sm text-danger-300">
          {failure}
        </p>
      ) : null}

    </Card>
  );
}
