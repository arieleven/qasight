---
type: spec
status: current
created: 2026-06-25
updated: 2026-11-02
---

# Phase 4 BYOK and Failure Analysis

<!-- source: notion https://www.notion.so/354d95cd0312800aafc1e117a94f6fca + https://www.notion.so/354d95cd03128001b96df447ecb59f2a -->

## Strategy (WHY)

- **BYOK:** Users supply their own LLM API keys (OpenAI, Anthropic, etc.) — no bundled provider billing.
- **No paid key required:** a third provider, `openai_compatible`, talks to any server that implements OpenAI's chat-completions API at a configurable base URL, so a free tier (Groq, OpenRouter, Gemini) or a local Ollama works too.
- **No key at all, locally:** `claude_code` runs the Claude Code CLI already installed and signed in on the machine (`claude -p`), so a Claude subscription works without an API key.
- **Failure summaries:** Failed runs should surface a short, actionable explanation on Run Details without reading full logs.

## Architecture

- `testo_core` builds bounded, redacted failure context from artifacts (`failure_context_builder.py`, `failure_analysis_service.py`).
- `testo_api` orchestrates AI calls and settings (`/api/v1/ai/*`, run `ai-summary` endpoints).
- React: settings page + summary card on Run Details.

### Providers

| `provider` | Endpoint | Key |
| --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | required (`OPENAI_API_KEY`) |
| `anthropic` | `https://api.anthropic.com/v1` | required (`ANTHROPIC_API_KEY`) |
| `openai_compatible` | `base_url` from the settings, required, `http(s)` only | optional (`OPENAI_COMPATIBLE_API_KEY`); sent as a bearer token when present |
| `claude_code` | the local `claude` CLI (subprocess, no HTTP) | none: uses the CLI's own login |

`openai_compatible` reuses the OpenAI provider with a different base URL (`OpenAiCompatibleProvider` in `testo_core/services/ai/providers/openai_provider.py`) and counts as configured once it has a base URL, because a local server needs no key. The settings page offers presets for Groq, OpenRouter, Gemini and Ollama and, for this provider, raises the timeout to 60 seconds and `max_output_tokens` to 2000: reasoning models such as Gemini spend output tokens on thinking, and 300 cut the summary off mid-sentence. An HTTP error from the provider carries the provider's own message (redacted, truncated) into the summary's `limitations`, so a wrong model name is visible on the card. A rejected key (HTTP 401 or 403) is reported as `provider_misconfigured`. `api_key_env_var` must look like a variable name, so a key pasted there by mistake is refused instead of being stored and shown back.

#### `claude_code` (local Claude Code CLI)

Opt-in, picked on the settings page ("Claude Code on this machine"); the default provider is still `openai`. `ClaudeCodeProvider` (`testo_core/services/ai/providers/claude_code_provider.py`) runs `claude --print --output-format json --tools "" --strict-mcp-config --disable-slash-commands --no-session-persistence [--model <model>]` with the prompt on stdin, `shell=False`, in an empty temporary directory, and reads `result` and `usage` from the JSON reply. The prompt holds test output, which is untrusted, so the CLI gets no tools and no MCP servers: it can only answer. The binary is `claude` on the API's `PATH`, or the path in `TESTO_CLAUDE_CLI`, an environment variable only, so the settings page can never choose which program runs. Model is an alias (`sonnet`, `haiku`) or a full model id; empty or `default` uses the CLI's default. The settings page sets the timeout to 120 seconds for it, since each summary starts a process.

Status counts it as configured once the CLI is found (`key_present` is `null`). Errors map to the usual codes: CLI missing or not logged in is `provider_misconfigured` (with the reason in `limitations`), the timeout is `provider_timeout`, a usage limit is `provider_rate_limited`, and an unknown flag (an old CLI) asks for `claude update`. It is meant for local runs (`testo ui`); a team server has no signed-in `claude` and reports it as not configured.

### Settings storage

The API keeps the AI settings (enabled, provider, model, key source, env var name, limits) in `artifacts/ai_settings.json`, so they survive a restart (`FileBackedAiSettingsStore` in `testo_core/services/ai/integration_settings.py`). `TESTO_AI_SETTINGS_PATH` moves the file; an empty value keeps settings in memory only, which is what the test suite and the static export use. A key typed into the settings page (`runtime_input`) is never written to disk and has to be entered again after a restart; a key from an environment variable is read on each call.

The settings page resets the model to the provider's default when the provider changes, and names the missing environment variable when no key is found. The Run Details card turns each `error_code` into a sentence and links to the settings page when the fix is there.

Security: opt-in default, redaction pipeline, keys never returned on read.

### Failing test source (opt-in)

`share_test_source` (off by default) adds a short, line-numbered excerpt of each failing test's own code to the summary prompt: its definition and a few lines around the trace frames, inside the run's checkout only, secret-looking files skipped, at most 3 files and 4,000 characters. With a cloud provider the settings page warns that the excerpt is sent to it. See [Test Source in AI Summaries - 2026-11-01](Test%20Source%20in%20AI%20Summaries%20-%202026-11-01.md). See [Technical Debt Tracker](../Testing%20Workflows/Technical%20Debt%20Tracker.md) for ongoing contract gaps.

## Context-aware failure analysis

On run failure, extract failing Allure cases + log tail → `failure_context_v1` metadata → optional auto-generate summary → display via `GET /api/v1/runs/{run_id}/ai-summary`. Implementation: `testo_core/services/failure_context_builder.py`, `testo_api/execution_manager.py`.

## Release gate

[Release Checklist - Phase 4 AI and Failure Analysis](../Archive/Release%20Checklist%20-%20Phase%204%20AI%20and%20Failure%20Analysis.md)

---
**Context & Links:** [Architecture Overview](../Architecture/Architecture%20Overview.md), [Command Reference](../CLI%20Commands/Command%20Reference.md), [Product Roadmap § Phase 4: Next-Gen Capabilities](../Roadmap%20&%20Strategy/Product%20Roadmap.md#how-it-got-here)
