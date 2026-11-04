---
type: spec
status: current
created: 2026-10-31
updated: 2026-11-04
---

# Engineering Health Review - 2026-10-31

[Index](../Index.md) · [Specs & ADRs](README.md)

A pass over the test suite, CI, docs accuracy and dependencies on `main` at `42dab2f` (fixes merged as `32c1016`), 2026-10-31. It complements the [Project Review](../project-review.md) (code, architecture, security) and the [Product Review](../product-review.md); findings already accepted there (for example D4, no Python lock file) are not repeated. Small fixes landed in [#222](https://github.com/arieleven/qasight/pull/222) (C1 in [#213](https://github.com/arieleven/qasight/pull/213), found by a parallel review the same day); the rest are proposals, ranked by how much they cost a contributor or user today.

## Verdict

The engineering base is healthy. The fast suite (1286 tests) passes in random order, on 4 to 8 parallel workers and on Python 3.14; `main` had no flaky CI failure in the last 100 workflow runs; the CLI reference matches every command and option, and the Getting Started flow runs end to end. What this pass found is mostly at the edges: CI that silently tests less than it looks like (the heavy tier and the nightly), Dependabot PRs that go red for config reasons, a CLI typo that looked like failing tests, and a connection leak that only showed as warnings.

## What was checked

| Check | Result |
|-------|--------|
| `pytest -m "tier_fast and not quarantined"` (Python 3.13) | 1270 passed, 3 skipped, 83.6% coverage, 114 s serial on the review machine |
| Same suite, `pytest-randomly` (3 seeds) and `pytest-xdist` (`-n 4`, `-n 8`, 5 runs) | Green, except one parallel-only failure (T3, fixed). 41 s with `-n 4` |
| Same suite on Python 3.14.6 | 1278 passed |
| Heavy tier (`tier_heavy and not tier_external`) | 25 passed in 4 s |
| Frontend: `npm ci`, typecheck, ESLint, vitest (98 tests), build | Green; one timeout under load (T4, fixed) |
| `ruff`, `ruff format --check`, `mypy`, `docs_vault.py lint` | Clean |
| Last 100 GitHub Actions runs | `ci`: 20 green, 3 red (two Dependabot config failures, one PR that forgot `updated` dates, which is the lint working), 2 cancelled as superseded. No red run on `main` |
| CLI reference against the Typer tree (every command and `--option`) | Matches; `qasight lease enable` is only in the Shared Resource Pools guide |
| Getting Started, by hand in a clean folder | Works; prints a confusing `workers` warning (U1) |
| Relative links in README, CONTRIBUTING, examples, integrations | All resolve. External links could not be checked from the review sandbox |
| TypeScript 6 and 7 against the frontend | Both compile it cleanly; both blocked by tooling peers (P1) |
| `npm audit` | One moderate advisory in dev-only lint tooling (P6) |

## Fixed in #222

| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| C1 | High | **A CLI typo looked like failing tests.** `main()` runs Typer with `standalone_mode=False`, so Click's usage errors escaped: `qasight rnu`, `qasight run --cylce web` or a missing argument printed a traceback and exited `1` (`DOMAIN_FAILURE`, "tests failed"), not the documented `2`. A CI job with a typo went red as if tests had failed | Fixed in #213: Click's usage message and exit `2` |
| R1 | Medium | **Database connections leaked.** `reset_engine_cache()` cleared an `lru_cache` without `dispose()`, LRU eviction did the same, and `qasight doctor` / `qasight config-db` never closed their probe engine. The fast suite printed 110 `unclosed database` warnings | A dict cache that disposes on reset, `dispose()` after each probe, one `engine_kwargs()` instead of three copies; a test checks the pooled connections are closed |
| D1 | Medium | **Dependabot PRs went red for config reasons.** #211 bumped `setup-python` in the root `action.yml` only, failing the copies-match contract; #210 failed `changelog required` because Dependabot adds its `no-changelog` label just after `opened`; #210 also moved `@types/node` to 26 while CI builds on Node 22 | Dependabot watches both action copies; `changelog required` skips Dependabot by author; `@types/node` majors are ignored and move with Node by hand; `setup-python` v7 in both copies |
| C2 | Medium | **The heavy tier never ran.** `pr-heavy.yml` only runs on the `e2e-heavy` label or by hand (skipped in all 3 recent triggers), so its 25 tests could rot unseen. They take 4 s | A step in the `test` job runs them on every PR and push |
| C3 | Low | **CI waited on lint.** `test` and the version matrix had `needs: format`, so every run spent ~1 min on lint before starting the 2 min test job, and no `pip` cache was used | Jobs run in parallel; `cache: pip` on every `setup-python` that installs the project |
| C4 | Low | **The coverage gate was 19 points below the suite.** Gate 65%, suite 84%, so coverage could fall a long way without CI noticing | Gate 80% |
| C5 | Low | **Python 3.14 was untested.** Released a year ago; not in the matrix or classifiers | Added to both; the suite passes |
| T3 | Low | **A parallel-only flake.** The report-origin contract tests share one folder under `STATIC_HISTORY_ROOT`; under `pytest-xdist` one worker's teardown deleted the page another was reading (1 of 5 runs) | One folder per process |
| T4 | Low | **The UI happy-path test flaked under load.** 1.5 s alone; on a busy machine it passed vitest's 5 s test timeout, and on a CI runner a `waitFor` gave up at testing-library's 1 s default ("Unable to find role=link"), because `getByRole` on a full jsdom page is slow | 15 s timeout for this five-page test; `asyncUtilTimeout` 5 s for every test in `src/test/setup.ts` |
| T5 | Low | **Warning noise.** pytest tried to collect `TestCaseRecord` as a test class, and the sandbox JWT secret was under PyJWT's 32-byte minimum, which warned on every token | `__test__ = False`; a 40-byte test secret |
| X1 | Low | **Small docs drift.** Getting Started showed `qasight 1.1.1`; `ci.yml`'s deploy job pointed at "the Sprint 4 publish workflows from PR #38" | Version-free example; the comment names `publish.yml`, `docker-publish.yml`, `move-major-tag.yml` |

## Proposals

Ranked, as written on 2026-10-31. All were taken up on 2026-11-01: see [Follow-up](#follow-up-2026-11-01).

| # | Severity | Finding | Proposal | Size |
|---|----------|---------|----------|------|
| U1 | Medium | **The first run warns about something the user never set.** `qasight run --cycle unit` from Getting Started prints `pytest: workers=4 ignored, pytest-xdist is not installed; running serially.`: `workers` defaults to 4, so every new user without `pytest-xdist` sees it on their first run | Warn only when `workers` is set in `qasight.yaml` (carry "set explicitly" on the stage), or log the default case at debug level. Touches `config/sections.py`, `config/schema.py`, `frameworks/pytest_adapter.py` and the Command Reference | S |
| P1 | Medium | **TypeScript 6 and 7 are blocked by two packages, not one.** The project review names `typescript-eslint` (peer `<6.1`). `openapi-typescript` 7.13, the latest, also has a `typescript ^5.x` peer, so `npm ci` refuses TypeScript 6 (seen in #222). TypeScript 7 already compiles the UI with no errors | Keep #106 open as the tracker, or close it with a note and let Dependabot reopen it; recheck both packages' peers monthly. No code change is waiting on us | — |
| C6 | Medium | **Per-file coverage is wrong for modules the pytest plugin imports early.** The `pytest11` entry point loads `qasight.pytest_plugin` before `pytest-cov` starts, so `config/schema.py`, `engine/events.py` and `engine/result.py` show 0% and `pytest_plugin.py` 13%; under `coverage run -m pytest` they are 98%, 100%, 100% and 36%. The total is about the same, but the per-file table points at the wrong gaps | Run the CI suite as `coverage run -m pytest` (with the gate in `coverage report --fail-under`), or start coverage earlier with `COV_CORE_SOURCE`. Then the real gap, `pytest_plugin.py` at 36%, is visible | S |
| C7 | Low | **The nightly is green but runs nothing.** `nightly-external` needs `TESTO_E2E_*` secrets; none are set, so all 5 recent nights were a passing no-op | Taltal's call: add a throwaway GitHub token and owner so it really runs, or drop the schedule so a green nightly doesn't suggest coverage | — |
| C8 | Low | **The fast suite could run in parallel.** 114 s serial, 41 s with `-n 4` on the review machine, and green in 5 parallel runs once T3 was fixed | Add `pytest-xdist` to the `dev` extra and `-n auto` in CI. Watch the first week for shared-state flakes | S |
| D2 | Low | **Container base images don't get update PRs.** All three Dockerfiles default to `python:3.11-slim-bookworm` (Debian 12, now oldstable), and Dependabot has no `docker` ecosystem | Add the `docker` ecosystem; move the defaults to a current Debian and Python in one PR, checked by the image scan and start jobs | S |
| D3 | Low | **Starlette is deprecating `httpx` in its test client.** Every API test prints `install httpx2 instead`; a future Starlette will drop it | Swap the `dev` dependency when FastAPI's test client moves; nothing to do yet | — |
| D4 | Low | **One moderate `npm audit` advisory.** `minimatch` below 11.0.1 (ReDoS) under ESLint and `typescript-eslint`. Dev tooling only, nothing ships to users. An `overrides` entry breaks their peer ranges | Wait for ESLint and `typescript-eslint` to move to `minimatch` 11 | — |
| D6 | Low | **The pre-commit hook and CI run different ruff versions.** `.pre-commit-config.yaml` pins ruff `v0.15.0`; CI installs `ruff>=0.15.0`, today 0.17.3. They agree on the repo now, but ruff changes its format style yearly, and then a hook-formatted commit fails `ruff format --check` in CI. Dependabot doesn't bump pre-commit revs | Pin ruff to one version in the `dev` extra and the hook, and bump both together (or run `pre-commit autoupdate` in the weekly deps PR) | XS |
| D5 | Low | **Two Postgres versions.** The root `docker-compose.yml` uses `postgres:15-alpine`, the team server compose file `postgres:16-alpine` | Use one version in both | XS |

## Follow-up, 2026-11-01

Taltal asked for all of the proposals. What each one became, in one pull request:

| # | Outcome |
|---|---------|
| U1 | Done. A stage carries `workers_explicit`; on the built-in default a pytest stage without `pytest-xdist` runs serially without a warning. `workers` on the stage, in `defaults:` or `--workers` still warns |
| C6 | Done, by a different fix than proposed: `import qasight` no longer loads `config` and `engine` eagerly (they resolve on first use), so the `pytest11` plugin imports nothing measurable before pytest-cov starts. `config/schema.py` now reports 99%, `engine/events.py` and `engine/result.py` 100%. `pytest_plugin.py` itself still loads before coverage starts and shows 14%; its function bodies are measured when they run |
| C7 | Done without a token: the nightly job runs only when the repository variable `TESTO_E2E_ENABLED` is `true`, so until Taltal adds the secrets and the variable it shows as skipped, not green |
| C8 | Done. `pytest-xdist` is in the `dev` extra and CI runs the fast suite with `-n auto` (1617 tests in 66 s on 4 cores, coverage 86.9%) |
| D2 | Done. The three Dockerfiles use `python:3.13-slim-trixie` (Debian 13) as literal `FROM` lines Dependabot can read, the server's UI stage `node:22-trixie-slim`; Dependabot has `docker` and `docker-compose` ecosystems (Postgres majors stay manual) |
| D5 | Done. Both compose files use `postgres:16-alpine`; an old local volume needs a dump and restore or `docker compose down -v` |
| D6 | Done. ruff is pinned (`ruff==0.17.3`) in the `dev` extra and the hook; `tests/contract/ci/test_ruff_pin.py` fails when they differ |
| P1 | Nothing to change yet, rechecked: `openapi-typescript` 7.13.0 still has peer `typescript ^5.x`, `typescript-eslint` 8.72.1 `<6.1.0`; TypeScript is at 7.0.2. #106 stays open as the tracker |
| D3 | Nothing to change yet: FastAPI's test client still uses `httpx` |
| D4 | Nothing to change yet: the same `minimatch` advisory, now listed under 13 dev-only packages (ESLint, `typescript-eslint` and `openapi-typescript` chains); none ship to users |

## Related

- [Project Review](../project-review.md): code, architecture and security scorecard
- [QA Strategies](../Testing%20Workflows/QA%20Strategies.md): test tiers and which workflow runs which
- [CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md): the workflows
- [Technical Debt Tracker](../Testing%20Workflows/Technical%20Debt%20Tracker.md): where accepted items live long term
