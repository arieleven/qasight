import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { apiClient, type AskTurnItem } from "../../lib/api-client";
import { Badge, Button, Card, PageHeader, Spinner } from "../../components/ui";
import { IS_STATIC_BUILD, loadAskExamples } from "../../lib/static-backend";
import { DemoExampleNote } from "../../components/DemoExampleNote";
import { CyclePicker } from "../health/ProjectHealthPage";
import { AiNotReadyNote } from "../../components/AiNotReadyNote";

export const SUGGESTED_QUESTIONS = [
  "What broke in the latest runs, and is it new?",
  "Which tests are flaky, and do their failures have something in common?",
  "Which tests got slower, and by how much?",
  "Which cycle needs attention first?"
];

const STORAGE_KEY = "testo.askConversation";
// Earlier turns sent back with each question, so follow-ups ("and the slow ones?") make sense.
const HISTORY_SENT = 4;

type Turn = AskTurnItem & { model?: string | null };

function loadTurns(): Turn[] {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "[]") as Turn[];
  } catch {
    return []; // storage disabled or a bad value: start a new conversation
  }
}

function saveTurns(turns: Turn[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(turns));
  } catch {
    // Not kept when you leave the page; the conversation still works.
  }
}

/**
 * Ask about your tests in plain words. The configured AI provider answers from
 * the recorded runs (results, failure messages, flaky and slow tests, test
 * names), never from source code. The conversation stays in this browser tab.
 */
export function AskPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const cycle = searchParams.get("cycle") ?? "";
  const aiStatus = useQuery({ queryKey: ["ai-config-status"], queryFn: () => apiClient.getAiConfigStatus() });
  const [turns, setTurns] = useState<Turn[]>(loadTurns);
  const [question, setQuestion] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  // The read-only demo can't ask anything, so it shows a made-up conversation instead.
  const examples = useQuery({ queryKey: ["ask-examples"], queryFn: loadAskExamples, enabled: IS_STATIC_BUILD, staleTime: Infinity });
  const showExamples = IS_STATIC_BUILD && turns.length === 0 && (examples.data?.length ?? 0) > 0;
  const shownTurns: Turn[] = showExamples ? (examples.data ?? []) : turns;

  const ask = useMutation({
    mutationFn: (text: string) =>
      apiClient.askAboutTests(
        text,
        cycle || undefined,
        turns.slice(-HISTORY_SENT).map(({ question: q, answer }) => ({ question: q, answer }))
      ),
    onSuccess: (data) => {
      if (data.status !== "answered" || !data.answer) return;
      const next = [...turns, { question: data.question, answer: data.answer, model: data.model }];
      setTurns(next);
      saveTurns(next);
      setQuestion("");
    }
  });

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [turns.length, ask.isPending]);

  const aiReady = Boolean(aiStatus.data?.enabled && aiStatus.data?.configured);
  const canAsk = !IS_STATIC_BUILD && aiReady && !ask.isPending;
  const failure = ask.data?.status === "failed" ? ask.data.error_message : ask.error instanceof Error ? ask.error.message : null;

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (trimmed && canAsk) ask.mutate(trimmed);
  };
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit(question);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit(question);
    }
  };
  const clear = () => {
    setTurns([]);
    saveTurns([]);
    ask.reset();
  };

  return (
    <section className="space-y-6">
      <PageHeader
        title="Ask about your tests"
        subtitle="Questions in plain words, answered from the runs QASight recorded."
        actions={<CyclePicker cycle={cycle} onChange={(next) => setSearchParams(next ? { cycle: next } : {})} />}
      />

      <Card title="Conversation" actions={<Badge tone="warn">AI answers</Badge>} data-testid="ask">
        <p className="text-sm text-ink-300">
          Your AI provider reads the same results as the AI review (test names, failure messages and run statistics, never your source code). Answers can be
          wrong, so check the tests they name. Each question is one call to your provider.
        </p>

        {IS_STATIC_BUILD ? (
          <p className="mt-3 rounded-xl border border-white/10 bg-ink-800/60 px-3 py-2 text-sm text-ink-200" data-testid="ask-demo-note">
            This demo is read-only, so it can't ask your AI provider. Try it on your own project with <code className="font-mono text-xs">qasight ui</code> and an AI
            provider (a free Gemini key works).
          </p>
        ) : aiStatus.data && !aiReady ? (
          <AiNotReadyNote status={aiStatus.data} />
        ) : null}

        {showExamples ? (
          <DemoExampleNote
            className="mt-3"
            testId="ask-example-note"
            what="These answers were written from this demo's runs by a template, not by an AI, to show what a conversation looks like."
          />
        ) : null}

        <div className="mt-4 space-y-4" aria-live="polite">
          {shownTurns.map((turn, index) => (
            <div key={index} className="space-y-2" data-testid="ask-turn">
              <p className="ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-md bg-brand-600/30 px-3.5 py-2 text-sm text-white">{turn.question}</p>
              <div className="max-w-[95%] rounded-2xl rounded-bl-md border border-white/5 bg-ink-950/60 px-3.5 py-2.5">
                <p className="whitespace-pre-wrap break-words text-sm text-ink-100">{turn.answer}</p>
                {turn.model ? <p className="mt-1.5 text-xs text-ink-400">{turn.model}</p> : null}
              </div>
            </div>
          ))}
          {ask.isPending ? (
            <div className="flex items-center gap-2 text-sm text-ink-300">
              <Spinner /> Reading your runs… this can take up to a minute on a free provider.
            </div>
          ) : null}
          <div ref={endRef} />
        </div>

        {shownTurns.length === 0 && !ask.isPending ? (
          <div className="mt-2">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-400">Try asking</h3>
            <ul className="flex flex-wrap gap-2">
              {SUGGESTED_QUESTIONS.map((text) => (
                <li key={text}>
                  <button
                    type="button"
                    onClick={() => {
                      setQuestion(text);
                      submit(text);
                    }}
                    className="rounded-full border border-white/10 bg-ink-900/70 px-3 py-1.5 text-left text-xs text-ink-200 transition-colors hover:border-brand-500/60 hover:text-white"
                  >
                    {text}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <form onSubmit={onSubmit} className="mt-4 space-y-2">
          <label htmlFor="ask-question" className="sr-only">
            Your question
          </label>
          <textarea
            id="ask-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            maxLength={500}
            rows={2}
            placeholder={cycle ? `Ask about ${cycle}…` : "Ask about any cycle…"}
            className="w-full resize-y rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-ink-100 placeholder:text-ink-500"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" disabled={!canAsk || !question.trim()}>
              {ask.isPending ? <Spinner /> : null}
              Ask
            </Button>
            {turns.length > 0 ? (
              <Button variant="ghost" onClick={clear} disabled={ask.isPending}>
                New conversation
              </Button>
            ) : null}
            <span className="text-xs text-ink-400">Enter to ask, Shift+Enter for a new line.</span>
          </div>
        </form>
        {failure ? (
          <p role="alert" className="mt-3 text-sm text-danger-300">
            {failure}
          </p>
        ) : null}
      </Card>
    </section>
  );
}
