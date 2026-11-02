---
type: spec
status: current
created: 2026-11-01
updated: 2026-11-01
---

# Test Source in AI Summaries - 2026-11-01

Part of the AI failure analysis work ([Phase 4 BYOK and Failure Analysis](Phase%204%20BYOK%20and%20Failure%20Analysis.md), [Specs & ADRs](README.md)).

## Context

The AI failure summary on Run Details saw only the failure message, the trace and run statistics. For an assertion such as `assert 500 == 201` it could name the error but rarely say what the test was checking, so "Likely cause" was often "Not clear from the evidence". The summary is much better when the model can read the failing test itself. Sending source code is a privacy decision, though: with a cloud provider (OpenAI, Anthropic, Gemini through its OpenAI-compatible API) the code leaves the machine.

## Decision

- **One opt-in switch, off by default.** `share_test_source` in the AI settings (`AiIntegrationSettings`, saved in `ai_settings.json`), shown on the AI settings page as "Let the AI read the failing test's source". The CLI takes `testo ai setup --share-test-source`; CI takes `TESTO_AI_SHARE_TEST_SOURCE=1` next to `TESTO_AI_PROVIDER`. It applies only to the failure summary; the AI review and Ask still read test results only.
- **Provider-aware copy.** With `claude_code` the page says the excerpt goes to Claude through the person's own Claude Code login, like their other Claude Code sessions. With any other provider it shows a warning naming where the excerpt goes (OpenAI, Anthropic, or the base URL's host). Changing the provider on the page switches sharing off again, so turning it on is always a decision about one provider.
- **Only the failing test's own code** (`testo_core/services/failure_source.py`). For each failing test: the test's definition found by its name (Python `def`, Go `func`, Jest/Vitest `it(`/`test(`, Behave `Scenario:`, a method call), cut at 60 lines, plus 8 lines either side of each stack-trace frame in the same file. Excerpts are line-numbered. If neither the definition nor a frame is found, nothing is sent for that file: never a whole file.
- **Hard limits.** Files must resolve (after symlinks) inside the run's checkout (`target_repo`, else the cycle's latest recorded checkout), so `../` or absolute paths in a trace reach nothing else. Secret-looking paths are skipped (`.env*`, `*.pem`, `*.key`, `id_rsa`, `.netrc`, names with secret/credential/password/token, anything under `.git`, `.ssh`, `.aws` and similar), as are files over 512 KB, binary files and files containing a private key. At most 3 files, 2,000 characters per excerpt and 4,000 in all; every excerpt goes through the same redaction as failure logs. The source shares the prompt budget with the log, which gives up the room.
- **Prompt injection.** Test source is untrusted like test output. The prompt marks `[TestSource]` as data, not instructions, and the `claude_code` provider still runs with no tools.
- **Visible on the card.** The summary records `context_stats.source_files` and `source_chars`; Run Details says "Read the source of N test files" under a summary that used it.

## Consequences

- Better "Likely cause" and "Next steps" when the person allows it, with no change for anyone who doesn't.
- The privacy copy is accurate in both modes: the AI review and Ask still say they never read source code (true, they don't), and the settings page states what the summary sends when sharing is on.
- A run with no recorded checkout (some `testo ingest` runs from another machine) gets no source; the summary works as before.
- A team server's API reads the checkout it ran the tests in. Teams that upload results from CI get source only if the server also has the checkout at the recorded path.
