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
| Hypothesis, every importer | JUnit, TRX and Robot XML never broke. Jest, Playwright, k6 JSON with wrong-typed fields raise `TypeError`/`KeyError`; the import keeps the other files and reports the file as unreadable | Fixed in the third round (clear message) |
| mutmut on `flaky_service`, `retention`, `attention` against their own test files | 561 of 830 mutants killed (68%); weakest: `flaky_costs`, `classify`, `broken`, `needs_attention` | Tests added in the third round |
| Locust, single uvicorn process, SQLite | Fine at 20 users except `/report` (2.7 s alone) and `/dashboard/attention`; a 300-test ingest takes ~1.4 s and stalls other requests; at 100 users p50 is 6-8 s | Read-side cache in the third round |
| axe-core on 16 pages, desktop and phone | No `h1` on Compare and the test page while loading or empty; report small print 2.6:1; report tables not keyboard-scrollable | Fixed; 0 violations after |
| vulture, radon, pip-licenses | No dead code (one false positive); average complexity A (4.4) with 15 functions at E/F (`parse_stage` 55, `init` 53); no GPL, psycopg is LGPL (optional `db` extra) | Not changed |

### Third round

Taltal asked to fix the open items from the second round:

| Item | Fix |
|------|-----|
| Load | `qasight/history/memo.py` keeps the manager report, Needs attention and project health in memory, keyed on a fingerprint of the run table (one aggregate query on the promoted columns: count, newest start and end per status), so any new, finished or deleted run recomputes them. One computation per key at a time; `TESTO_READ_CACHE_S` (default 60 s, `0` off) bounds staleness from anything else. Locust, 50 viewers and one CI uploader for 60 s on SQLite: 143 requests without the cache, 809 with it; median 2.6 s to 0.16 s, p95 9.2 s to 1.2 s |
| CSP | The web UI gets a full policy: everything from `'self'` (plus `data:` and `blob:` images and `data:` fonts), `object-src 'none'`, `base-uri` and `form-action 'self'`. Styles keep `'unsafe-inline'` (React `style` props). The font is now `@fontsource/plus-jakarta-sans` bundled with the app instead of Google Fonts, which also removes the SRI finding. History reports and `/docs`/`/redoc` keep the old `frame-ancestors`-only policy, since they load their own scripts |
| Importers | A JSON file recognised as Jest, Playwright, RSpec, k6, xcresulttool or go test output with a wrong-typed field now reads "looks like a Jest/Vitest report, but a field is missing or has an unexpected type (...)". The fuzz run finds 0 unexpected exceptions over 2,700 files |
| Weak tests | `tests/unit/qasight/test_flaky_scoring.py` and new cases in `test_attention.py` pin counts, flip rates, wording, truncation and the broken-streak boundary. They found a bug: Needs attention and project health compared the run status with `"failed"` while it is stored as `"FAILED"`, so a run marked failed with exit code 0 showed green |
| Found on the way | Loading pages on demand broke the single-file HTML report (`qasight report --html`): its entry imported a shared chunk the file didn't contain. The export now inlines imported chunks as `data:` modules |

More API worker processes were not added: runs started from the UI execute in the API process (the in-process execution manager, its queue and SSE stream), and startup orphan cleanup and the retention thread assume one process. Several workers would need those moved out first, see the [Architecture Review](Architecture%20Review%20-%202026-10-31.md).

## Decision

- Keep the checks running: zizmor is a blocking step in the CI `format` job, with accepted findings listed and explained in `.github/zizmor.yml`; `scorecard.yml` runs OpenSSF Scorecard weekly and on push to `main`, results in Security > Code scanning.
- Scorecard's `publish_results` stays off, so nothing is sent to the public Scorecard API. Turning it on, enabling CodeQL default setup, and signing up for hosted tools (SonarCloud, Codacy, Snyk) are the maintainer's clicks.
- `ci.yml` keeps its pip and npm caches. It also runs on `release: published`, which makes zizmor treat it as a publisher, but nothing it builds ships.

## Consequences

- Actions no longer move under us between Dependabot PRs; a Dependabot PR is the only way an action version changes.
- `defusedxml` is a new runtime dependency (pure Python, no dependencies of its own).
- The first visit to a page other than the dashboard fetches one more small chunk.
- The report, Needs attention and project health can be up to `TESTO_READ_CACHE_S` old for changes that are not runs (a triage mark, a config edit).
- The app no longer loads anything from Google; a page that needs another origin has to be added to `APP_CSP` in `qasight_api/security.py`.

See [CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md) for what each workflow runs.
