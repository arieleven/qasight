---
type: spec
status: current
created: 2026-11-11
updated: 2026-11-11
related: "Security and Install Review - 2026-10-31, Engineering Health Review - 2026-10-31"
---

# External Audit Tools - 2026-11-11

[Index](../Index.md) · Taltal asked on 2026-11-11 to run the free third-party repo review tools on QASight and then to fix everything they found.

## Context

Run locally, with no accounts: gitleaks (all 687 commits), pip-audit (every extra), npm audit, Bandit, Semgrep community rules, zizmor (GitHub Actions), ruff, mypy, and Lighthouse on a local build of the Pages demo.

Clean: no real secrets (the 24 gitleaks hits are made-up tokens in the redaction tests), no known vulnerabilities in the Python dependencies, none in the frontend's production dependencies (10 moderate ones in eslint's dev tree), ruff and mypy pass, accessibility 100. Bandit's two `shell=True` calls run a command the user configured, and the SQL f-strings use fixed table names; both were already marked.

Found:

| Finding | Tool | Fix |
|---------|------|-----|
| The PyPI release build restored the npm cache, which a pull request could have written | zizmor | `package-manager-cache: false` in `publish.yml` |
| 73 actions pinned to a moving tag, one (`pypa/gh-action-pypi-publish`) to a branch | zizmor, Semgrep | Every third-party action pinned to a commit SHA with its version in a comment; Dependabot updates both |
| `${{ github.base_ref }}`, `${{ github.repository }}` and `${{ github.ref_name }}` expanded inside `run:` | zizmor, Semgrep | Passed through `env:` |
| Checkouts left the job token on disk | zizmor | `persist-credentials: false`, except `changelog-on-main.yml` and `move-major-tag.yml`, which push with it |
| JUnit, TRX and Robot XML parsed with `xml.etree` (reports arrive through API uploads too) | Bandit, Semgrep | Parsed with `defusedxml`; an entity declaration makes the file unreadable instead of being expanded |
| Demo: CLS 0.85 on mobile, one 610 kB bundle, no meta description; performance 46 mobile, 74 desktop | Lighthouse | The demo notice holds its export line's place, the main area is at least a screen tall, pages other than the dashboard load on demand (344 kB entry), meta description. Now 76 mobile, 98 desktop, CLS 0 |

### Second round

Taltal then asked for seven more kinds of checks, run the same day:

| Tool | Result | Fix |
|------|--------|-----|
| Schemathesis (1,782 generated API requests) | 3 requests answered 500: a dashboard limit of 2^63 (SQLite overflow), and validation errors whose bytes body or exception context the 422 handler could not serialize | Upper bounds on both dashboard limits; the 422 handler makes its error list JSON-safe. `tests/contract/api/test_api_fuzz_regressions.py` |
| OWASP ZAP 2.17 (OpenAPI import, spider, active scan) | No high findings, no injection or XSS. Medium: the CSP sets only `frame-ancestors`; no SRI on the Google Fonts stylesheet | Not changed: a full CSP needs its own design (inline styles, fonts, AI provider calls) |
| Hypothesis, every importer | JUnit, TRX and Robot XML never broke. Jest, Playwright, k6 JSON with wrong-typed fields raise `TypeError`/`KeyError`; the import keeps the other files and reports the file as unreadable | Not changed: the message is vague but nothing is lost |
| mutmut on `flaky_service`, `retention`, `attention` against their own test files | 561 of 830 mutants killed (68%); weakest: `flaky_costs`, `classify`, `broken`, `needs_attention` | Not changed |
| Locust, single uvicorn process, SQLite | Fine at 20 users except `/report` (2.7 s alone) and `/dashboard/attention`; a 300-test ingest takes ~1.4 s and stalls other requests; at 100 users p50 is 6-8 s | Not changed: matches the single-process API in the [Architecture Review](Architecture%20Review%20-%202026-10-31.md) |
| axe-core on 16 pages, desktop and phone | No `h1` on Compare and the test page while loading or empty; report small print 2.6:1; report tables not keyboard-scrollable | Fixed; 0 violations after |
| vulture, radon, pip-licenses | No dead code (one false positive); average complexity A (4.4) with 15 functions at E/F (`parse_stage` 55, `init` 53); no GPL, psycopg is LGPL (optional `db` extra) | Not changed |

## Decision

- Keep the checks running: zizmor is a blocking step in the CI `format` job, with accepted findings listed and explained in `.github/zizmor.yml`; `scorecard.yml` runs OpenSSF Scorecard weekly and on push to `main`, results in Security > Code scanning.
- Scorecard's `publish_results` stays off, so nothing is sent to the public Scorecard API. Turning it on, enabling CodeQL default setup, and signing up for hosted tools (SonarCloud, Codacy, Snyk) are the maintainer's clicks.
- `ci.yml` keeps its pip and npm caches. It also runs on `release: published`, which makes zizmor treat it as a publisher, but nothing it builds ships.

## Consequences

- Actions no longer move under us between Dependabot PRs; a Dependabot PR is the only way an action version changes.
- `defusedxml` is a new runtime dependency (pure Python, no dependencies of its own).
- The first visit to a page other than the dashboard fetches one more small chunk.

See [CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md) for what each workflow runs.
