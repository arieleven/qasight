import { FormEvent, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AiConfigStatus, apiClient, UpdateAiConfigRequest } from "../../lib/api-client";
import { PageHeader } from "../../components/ui";

const inputClass = "w-full min-w-0 rounded-xl border border-ink-600 bg-ink-950/80 px-3 py-2 focus:border-brand-500 focus:outline-none text-sm text-ink-100";
const labelClass = "grid gap-1 text-xs font-medium text-ink-300";

type Provider = UpdateAiConfigRequest["provider"] & string;

// Mirrors the API's defaults (testo_core/services/ai/config.py).
const PROVIDER_DEFAULTS: Record<Provider, { model: string; envVar: string; timeoutS: number; maxOutputTokens: number }> = {
  openai: { model: "gpt-4.1-mini", envVar: "OPENAI_API_KEY", timeoutS: 8, maxOutputTokens: 300 },
  anthropic: { model: "claude-haiku-4-5", envVar: "ANTHROPIC_API_KEY", timeoutS: 8, maxOutputTokens: 300 },
  // Free tiers and local models answer more slowly than the paid APIs, and reasoning models
  // (Gemini) spend output tokens on thinking, which cuts a 300-token summary short.
  openai_compatible: {
    model: "llama-3.3-70b-versatile",
    envVar: "OPENAI_COMPATIBLE_API_KEY",
    timeoutS: 60,
    maxOutputTokens: 2000
  },
  // The local Claude Code CLI (`claude -p`) with its own login: no key. It starts a
  // process per summary, so it needs a longer timeout than an HTTP call.
  claude_code: { model: "sonnet", envVar: "", timeoutS: 120, maxOutputTokens: 300 }
};

// Services that speak OpenAI's chat-completions API. The first is the default.
const COMPATIBLE_PRESETS = [
  { id: "groq", label: "Groq (free key)", baseUrl: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile" },
  {
    id: "openrouter",
    label: "OpenRouter (free key, :free models)",
    baseUrl: "https://openrouter.ai/api/v1",
    model: "meta-llama/llama-3.3-70b-instruct:free"
  },
  {
    id: "gemini",
    label: "Google Gemini (free key)",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    model: "gemini-flash-latest"
  },
  { id: "ollama", label: "Ollama (local, no key)", baseUrl: "http://localhost:11434/v1", model: "llama3.2" }
] as const;

function describeStatus(status: AiConfigStatus): string {
  if (status.provider === "claude_code") {
    if (!status.configured) {
      return "Not configured: the claude command was not found on the API's PATH. Install Claude Code and log in, or set TESTO_CLAUDE_CLI to its path, then restart the API.";
    }
    if (!status.enabled) {
      return "Claude Code found, but AI failure summaries are switched off.";
    }
    return "Configured: failed runs can generate an AI summary with your Claude Code login.";
  }
  if (status.provider === "openai_compatible") {
    if (!status.configured) {
      return "Not configured: an OpenAI-compatible provider needs a base URL.";
    }
    if (!status.enabled) {
      return "Base URL saved, but AI failure summaries are switched off.";
    }
    return status.key_present
      ? "Configured: failed runs can generate an AI summary."
      : "Configured without an API key. That works for a local server such as Ollama; hosted services need a key.";
  }
  if (!status.configured) {
    if (status.api_key_source === "env") {
      const envVar = status.api_key_env_var ?? PROVIDER_DEFAULTS[status.provider].envVar;
      return `Not configured: ${envVar} is not set in the API's environment. Set it and restart the API, or switch to Runtime input.`;
    }
    return "Not configured: no API token is stored. Enter one above and save. It is kept in memory only, so enter it again after the API restarts.";
  }
  if (!status.enabled) {
    return "API key found, but AI failure summaries are switched off.";
  }
  return "Configured: failed runs can generate an AI summary.";
}

function providerLabel(provider: Provider, baseUrl: string | null | undefined): string {
  if (provider === "openai") return "OpenAI";
  if (provider === "anthropic") return "Anthropic";
  if (provider === "claude_code") return "Claude through your Claude Code login";
  try {
    return baseUrl ? new URL(baseUrl).host : "the provider's server";
  } catch {
    return "the provider's server";
  }
}

/** What turning on test source sharing sends, and where, for the chosen provider. */
export function describeSourceSharing(provider: Provider): string {
  const what =
    "Off by default. When on, each AI failure summary also gets a short excerpt of the failing tests' own code: " +
    "the test function and a few lines around the failing line, at most 3 files and about 4,000 characters, " +
    "redacted like the logs. Never whole files or the rest of the repository, and never .env files, keys or certificates.";
  if (provider === "claude_code") {
    return `${what} With Claude Code it goes to Claude through your own login, the same way your Claude Code sessions do; nothing else is sent anywhere.`;
  }
  return `${what} With this provider the excerpt is sent to its servers along with the failure text.`;
}

export function AIIntegrationSettingsPage() {
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");

  const statusQuery = useQuery({
    queryKey: ["ai-config-status"],
    queryFn: () => apiClient.getAiConfigStatus()
  });

  const status = statusQuery.data;
  const [formState, setFormState] = useState<UpdateAiConfigRequest>({
    enabled: false,
    provider: "openai",
    model: "gpt-4.1-mini",
    api_key_source: "env",
    api_key_env_var: null,
    timeout_s: 8,
    retry_count: 1,
    max_input_chars: 16000,
    max_output_tokens: 300,
    base_url: null,
    share_test_source: false
  });

  useEffect(() => {
    if (!status) {
      return;
    }
    setFormState({
      enabled: status.enabled,
      provider: status.provider,
      model: status.model,
      api_key_source: status.api_key_source,
      api_key_env_var: status.api_key_env_var,
      timeout_s: status.timeout_s,
      retry_count: status.retry_count,
      max_input_chars: status.max_input_chars,
      max_output_tokens: status.max_output_tokens,
      base_url: status.base_url,
      share_test_source: status.share_test_source ?? false
    });
  }, [status]);

  if (statusQuery.isLoading) {
    return <p className="text-sm text-ink-300">Loading AI settings…</p>;
  }
  if (statusQuery.isError || !status) {
    return <p className="text-sm text-danger-400">Couldn't load AI settings. Check that the API is running, then reload.</p>;
  }

  const effectiveForm = formState;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveState("saving");
    setSaveError(null);
    try {
      await apiClient.updateAiConfig({
        ...effectiveForm,
        api_key_input: apiKeyInput || undefined
      });
      setApiKeyInput("");
      setSaveState("saved");
      await statusQuery.refetch();
    } catch (error) {
      setSaveState("error");
      setSaveError(error instanceof Error ? error.message : "Failed to save settings.");
    }
  }

  return (
    <section className="space-y-4">
      <PageHeader title="AI settings" subtitle="Bring your own key. Keys are masked and never returned by the API." />

      <form onSubmit={onSubmit} className="grid w-full min-w-0 max-w-lg gap-3 glass-card p-4">
        <label className="flex items-center gap-2 text-sm text-ink-300">
          <input
            type="checkbox"
            checked={effectiveForm.enabled}
            onChange={(event) => setFormState((prev) => ({ ...prev, enabled: event.target.checked }))}
          />
          Enable AI failure summaries
        </label>
        <label className={labelClass}>
          Provider
          <select
            className={inputClass}
            value={effectiveForm.provider}
            onChange={(event) => {
              // A model name belongs to one provider, so switching provider resets it.
              // Picking a provider means you want AI on, so it also ticks Enable (untick to keep it off).
              // Sharing test source is a choice about one provider, so a new provider starts with it off.
              const provider = event.target.value as Provider;
              setFormState((prev) => ({
                ...prev,
                enabled: true,
                provider,
                model: PROVIDER_DEFAULTS[provider].model,
                timeout_s: PROVIDER_DEFAULTS[provider].timeoutS,
                max_output_tokens: PROVIDER_DEFAULTS[provider].maxOutputTokens,
                base_url: provider === "openai_compatible" ? COMPATIBLE_PRESETS[0].baseUrl : null,
                share_test_source: false
              }));
            }}
          >
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="openai_compatible">OpenAI-compatible (Groq, OpenRouter, Gemini, Ollama)</option>
            <option value="claude_code">Claude Code on this machine (your login, no key)</option>
          </select>
        </label>
        {effectiveForm.provider === "claude_code" ? (
          <p className="text-xs text-ink-400">
            Runs <code>claude -p</code> on the machine where the API runs, with no tools, so it only reads what it is
            given: the failure text, plus the failing tests' source if you allow it below. Meant for local use
            (<code>testo ui</code>). Model can be an alias such as sonnet or haiku, or empty for Claude Code's default.
          </p>
        ) : null}
        {effectiveForm.provider === "openai_compatible" ? (
          <>
            <label className={labelClass}>
              Service
              <select
                className={inputClass}
                value={COMPATIBLE_PRESETS.find((preset) => preset.baseUrl === effectiveForm.base_url)?.id ?? "custom"}
                onChange={(event) => {
                  const preset = COMPATIBLE_PRESETS.find((item) => item.id === event.target.value);
                  if (preset) {
                    setFormState((prev) => ({ ...prev, base_url: preset.baseUrl, model: preset.model }));
                  }
                }}
              >
                {COMPATIBLE_PRESETS.map((preset) => (
                  <option key={preset.id} value={preset.id}>
                    {preset.label}
                  </option>
                ))}
                <option value="custom">Custom base URL</option>
              </select>
            </label>
            <label className={labelClass}>
              Base URL
              <input
                className={inputClass}
                value={effectiveForm.base_url ?? ""}
                placeholder="https://host/v1"
                onChange={(event) => setFormState((prev) => ({ ...prev, base_url: event.target.value || null }))}
              />
            </label>
          </>
        ) : null}
        <label className={labelClass}>
          Model
          <input
            className={inputClass}
            value={effectiveForm.model}
            onChange={(event) => setFormState((prev) => ({ ...prev, model: event.target.value }))}
          />
        </label>
        {effectiveForm.provider === "claude_code" ? null : (
          <>
            <label className={labelClass}>
              API key source
              <select
                className={inputClass}
                value={effectiveForm.api_key_source}
                onChange={(event) =>
                  setFormState((prev) => ({ ...prev, api_key_source: event.target.value as "env" | "runtime_input" }))
                }
              >
                <option value="env">Environment variable</option>
                <option value="runtime_input">Runtime input</option>
              </select>
            </label>
            {effectiveForm.api_key_source === "env" ? (
              <label className={labelClass}>
                Name of the environment variable holding the key (not the key itself)
                <input
                  className={inputClass}
                  value={effectiveForm.api_key_env_var ?? ""}
                  placeholder={PROVIDER_DEFAULTS[effectiveForm.provider ?? "openai"].envVar}
                  onChange={(event) => setFormState((prev) => ({ ...prev, api_key_env_var: event.target.value || null }))}
                />
              </label>
            ) : (
              <label className={labelClass}>
                API token
                <input
                  type="password"
                  className={inputClass}
                  value={apiKeyInput}
                  placeholder="Enter token (not persisted to disk)"
                  onChange={(event) => setApiKeyInput(event.target.value)}
                />
              </label>
            )}
          </>
        )}
        <fieldset className="grid gap-1 rounded-xl border border-ink-700 p-3">
          <label className="flex items-start gap-2 text-sm text-ink-300">
            <input
              type="checkbox"
              className="mt-1"
              checked={effectiveForm.share_test_source ?? false}
              onChange={(event) => setFormState((prev) => ({ ...prev, share_test_source: event.target.checked }))}
            />
            <span>Let the AI read the failing test's source</span>
          </label>
          <p className="text-xs text-ink-400">{describeSourceSharing(effectiveForm.provider as Provider)}</p>
          {effectiveForm.share_test_source && effectiveForm.provider !== "claude_code" ? (
            <p className="text-xs font-medium text-warn-300">
              Source excerpts will be sent to {providerLabel(effectiveForm.provider as Provider, effectiveForm.base_url)}.
              Only switch this on if your code may leave this machine.
            </p>
          ) : null}
        </fieldset>
        <div>
          <button
            type="submit"
            disabled={saveState === "saving"}
            className="rounded-xl bg-brand-600 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saveState === "saving" ? "Saving…" : "Save settings"}
          </button>
        </div>
      </form>

      <p role="status" className="text-sm text-ink-300">
        {describeStatus(status)}
      </p>
      <p className="text-xs text-ink-400">
        Saved provider: {status.provider} | Model: {status.model}
      </p>
      {saveState === "saved" ? <p className="text-sm text-success-400">Settings saved.</p> : null}
      {saveState === "error" ? <p className="text-sm text-danger-400">Save failed: {saveError}</p> : null}
    </section>
  );
}
