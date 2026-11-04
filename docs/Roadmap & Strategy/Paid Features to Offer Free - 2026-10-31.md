---
type: roadmap
status: current
created: 2026-10-31
updated: 2026-11-04
---

# Paid features to offer free (2026-10-31)

[Index](../Index.md) · [Product Roadmap](Product%20Roadmap.md) · [Feature Gaps and Quick Wins](Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md) · [Compared With Other Tools](../Compared%20With%20Other%20Tools.md)

This note follows on from [Feature Gaps and Quick Wins](Feature%20Gaps%20and%20Quick%20Wins%20-%202026-10-31.md), which lists what the market treats as standard (gaps G1 to G14). This one asks something narrower: **which features do competitors keep behind a paid plan, and which of them could QASight give away, free and self-hosted?** Where a gap is already in that note, it is named here by its number and not described again. Nothing here is built; every item is a proposal that waits on the maintainer's go-ahead.

Effort uses the same scale: **S** is under a day, **M** is one to three days, **L** is one to two weeks.

## How it was checked

Pricing pages and docs, read on 2026-10-31: ReportPortal (SaaS pricing, features, quality gates), Allure TestOps (server pricing), Qualflare (plans), BuildPulse (prices from a comparison page, not BuildPulse's own), Currents, Datadog Test Optimization (via a comparison page), Trunk (billing), LambdaTest (now TestMu AI), BrowserStack Test Reporting & Analytics, TestRail, Testmo and Buildkite Test Engine. Prices change often and several pages didn't say which plan a feature needs; where a tier is uncertain, the table says so. Treat prices as rough.

## Where the paywalls are

| Tool | Paid plans | What only the paid or top plans get |
|---|---|---|
| ReportPortal | Free self-hosted; SaaS from $569/month | **Quality gates** (only with a managed-services subscription), more project spaces ($2,659/month for "multiple"), retention past 12 months |
| Allure TestOps | $39 per user per month, cloud or on-premise | The whole product: test case management, defects, dashboards, launch history. Allure Report itself is free |
| Qualflare | Free (1 user, 1 project, 30 launches, 30-day retention); $19 and $59 per user | Flaky detection, AI failure clustering and launch analysis ($19); test suite optimisation and audit logs ($59); SAML SSO and **custom branding** (Enterprise) |
| BuildPulse | From about $99/month | Flaky detection and quarantine, code coverage tracking; REST API only on a higher plan |
| Currents | $49 and $99/month plus per-result fees | SSO and SCIM ($99); **self-hosting, data redaction and bring-your-own S3 storage** (Enterprise only); retention capped at a year |
| Datadog Test Optimization | About $20 per active committer per month | Everything; **code coverage** is a separate add-on of about $8 to $12 per committer |
| Trunk Flaky Tests | Free up to 5 committers; per seat after | SSO and admin controls (Enterprise) |
| LambdaTest (TestMu AI) | Per parallel session or per user | Flaky detection and muting ($103 per parallel); AI root-cause analysis capped at 10 a month; **scheduled insight reports** (Test Manager Premium, $49 per user) |
| BrowserStack Test Reporting & Analytics | Bundled with Automate plans | **Custom dashboard widgets, AI failure analysis and quality gates** reported as Pro tier; also smart tags, custom alerts, re-run from the dashboard, mute and custom views (tier not stated in the docs) |
| TestRail | $22 to $78 per user per month | Enterprise only: SSO, audit logs, **automatic daily backups**, **cross-project reporting**, test case versioning and approvals |
| Testmo | $59, $249 and $599/month | Reporting centre and custom roles ($249); SSO, two-factor and audit log ($599) |
| Buildkite Test Engine | Free, $30 per user, Enterprise | Test history: 24 hours free, 7 days Pro, 28 days Enterprise; custom quarantine rules (Enterprise) |

## Already free in QASight

Worth saying out loud on the [comparison page](../Compared%20With%20Other%20Tools.md), because these are what people pay for elsewhere:

| Feature | Paid elsewhere | QASight |
|---|---|---|
| Self-hosting | Currents Enterprise; Allure TestOps $39 per user | One container, or `pip install` |
| Flaky detection and quarantine | Qualflare $19 per user, LambdaTest $103 per parallel, BuildPulse | `qasight flaky`, `quarantine:` in config |
| Unlimited history, users and projects | Buildkite 7 or 28 days, Qualflare 30 days and 1 user free, ReportPortal 12 months on Startup | No limits (and no retention yet, see G9) |
| Audit log and roles | Testmo $599/month, TestRail Enterprise, Qualflare $59 per user | `TESTO_AUTH=accounts` with `audit_event` |
| AI failure summaries | LambdaTest 10 a month, Qualflare by credits | Bring your own key, no cap |
| Test impact analysis and test splitting | Datadog, Currents, Buildkite | `qasight affected`, `qasight split` |
| REST API | BuildPulse higher plan | `/api/v1/`, every plan of one |

## Gaps already ranked, now with price tags

These are in the gaps note already. The price tags make the case for doing them free:

| Gap | Paid where |
|---|---|
| G8 Quality gates | ReportPortal (managed services only), BrowserStack (Pro). Allure Report 3 gives a free version, so free is now the bar |
| G11 SSO | Testmo $599/month, TestRail Enterprise, Currents $99, Qualflare Enterprise, Trunk Enterprise |
| G2 Quarantine from the UI, with rules | LambdaTest muting $103 per parallel, Buildkite custom rules on Enterprise |
| G5 Failure clusters | Qualflare $19 per user |
| G6 Cause categories | BrowserStack automatic failure analysis, Allure TestOps defects |
| G9 Retention and long history | Every tool caps history by plan; QASight's pitch is long history free, so retention must stay opt-in |
| G4 MCP server | Currents, on paid plans only |

## New ideas, ranked

Paid features that aren't in the gaps note. Ranked by value to a self-hosting team against effort.

| Rank | Idea | Paid where | What QASight would do | Effort |
|:---:|---|---|---|:---:|
| F1 | **Code coverage history and pull request coverage** | Datadog add-on (about $8 to $12 per committer), BuildPulse | `qasight ingest` also reads Cobertura, LCOV, JaCoCo and `go test -coverprofile` files. Each run stores a total and per-file numbers; the cycle page shows the trend; the pull request comment adds "coverage 81.2% (−0.4 against `main`)". Diff coverage (changed lines only) can follow, reusing what `qasight affected` knows about changed files | M |
| F2 | **Weekly digest** | LambdaTest scheduled reports ($49 per user), Testmo reporting centre ($249/month) | `qasight notify --digest week` posts to the Slack and Teams hooks that already exist: new flaky tests, tests fixed, the five that got slower, pass rate against last week. A cron line or a scheduled CI job runs it; no scheduler in the server | S |
| F3 | **Automatic labels** | BrowserStack smart tags | Labels on the test page and filters on Runs: *flaky*, *always failing* (broken, from G1), *new failure*, *got slower* (the timing noise floor already decides this), plus user labels from config. Most signals exist; this names them and makes them filterable | S–M |
| F4 | **Alert rules** | BrowserStack custom alerts | An `alerts:` list per cycle: "flake rate over 5%", "pass rate under 95%", "duration up 20% against `main`", each warning or critical, sent through the existing Slack and Teams alerts. Pairs with G13's webhooks | S–M |
| F5 | **Redaction before storing** | Currents (Enterprise only) | Run the existing `qasight/security/redaction.py` patterns, which today only clean what goes to the AI, over failure messages, stack traces and log attachments at ingest, with extra patterns from config. A strong line for "your data stays yours" | S |
| F6 | **Backup and restore** | TestRail (Enterprise only) | `qasight backup` and `qasight restore` for SQLite (one file copy) and a documented `pg_dump` sidecar in the team server compose file | S |
| F7 | **Suite clean-up report** | Qualflare test suite optimisation ($59 per user) | "Slow tests that haven't failed in 90 days" (candidates for a nightly cycle), "tests that stopped running", "duplicate names". Read-only, from history already stored | S–M |
| F8 | **Saved views and a by-repository overview** | TestRail cross-project reporting (Enterprise), ReportPortal multiple projects ($2,659/month), BrowserStack custom views | Filters on Runs and the dashboard kept in the URL, so a view is a link to share; a dashboard grouping by the `repository` that runs already record. Custom widgets stay out (L, low value) | S–M |
| F9 | **Previous failures first** | Currents orchestration (failed-first, fail-fast) | `pytest --qasight-failed-first` orders the tests that failed or flaked recently on the target branch first, using the team server's history rather than one machine's cache, so a red build turns red in the first minute | S |
| F10 | **Attachments in S3-compatible storage** | Currents (Enterprise only) | `TESTO_ATTACHMENTS_URL=s3://…` for traces, screenshots and videos, so a busy team server doesn't fill its disk; works with MinIO, R2 or S3. Goes with G9 | M |
| F11 | **Re-run failed tests from the UI** | BrowserStack re-run, Currents re-run failed | A button on the run page that calls GitHub's "re-run failed jobs" (or GitLab's retry) for the run's recorded CI job. Needs a CI token stored on the server, so it sits behind the `runner` role and its own switch | M |
| F12 | **Custom name, logo and colour** | Qualflare (Enterprise only) | `TESTO_BRAND_NAME`, `TESTO_BRAND_LOGO` and an accent colour for a team server. Cheap, but few people will ask | S |

> [!note] Built 2026-11-01
> F1 (line coverage totals per run, on the PR comment, run page and digest; no per-file numbers or trend chart yet), F2, F5, F6 and F9 are built. See [Free Copies of Paid Features - 2026-11-01](../Specs%20&%20ADRs/Free%20Copies%20of%20Paid%20Features%20-%202026-11-01.md).

### Suggested order

1. F2, F5, F6 and F9: each is small and each turns a paid line into a free one.
2. F1 (coverage): the biggest single "we pay Datadog extra for this".
3. F3 and F4 together, after G1 makes *flaky* and *broken* right.
4. F7 and F8 when there's a team server with real users.
5. F10 and F11 with G9, before a busy team server; F12 when someone asks.

## Copy ideas, never code or branding

Ideas and features aren't protected by copyright, so building any of the above is fine. Code, words, layouts and names can be. The rules:

- **Proprietary products: ideas only.** Allure TestOps, Currents, Trunk, BuildPulse, Datadog, LambdaTest, BrowserStack, TestRail, Testmo, Qualflare and Buildkite are closed. Don't read their minified front-end code, don't copy their UI layouts, icons, screenshots or help-page wording, and write our docs in our own words.
- **Open-source competitors: still reimplement.** ReportPortal and Allure Report are Apache 2.0. Their code could legally be reused with the licence and NOTICE kept, but the project's rule is to build from scratch, so every module has one licence (MIT) and one author's design. Reading their docs to understand a feature is fine; copying code is not. Reading their public file formats (Allure results, already imported) is fine.
- **Their names are their trademarks.** Never use a competitor's product or feature name in our UI, flags, config keys or package names: not "Smart Tags" (BrowserStack), "Auto-Analysis" (ReportPortal), "Intelligent Test Runner" (Datadog), "Currents Actions", "HyperExecute" or "Quo Agent". Use plain words: *automatic labels*, *failure clusters*, *affected tests*, *quarantine*. Generic terms such as *quality gate*, *flaky test* and *test impact analysis* are fine. Naming a competitor to compare facts, as the comparison page does, is fine; their logos are not.
- **Open formats are free to read.** JUnit XML, Cobertura, LCOV, JaCoCo XML and Go cover profiles are public formats, so F1 needs no licence check.
- **Patents weren't checked.** The one area to check before building is ML-based *predictive* test selection (Launchable, now CloudBees Smart Tests, and Datadog). `qasight affected` is rule-based and doesn't do that; keep it that way unless a check says otherwise.

## Out of scope, on purpose

- **Test case management**, versioning, approvals and datasets (TestRail Enterprise, Testmo, Allure TestOps, Qualflare). A different product, as the [comparison page](../Compared%20With%20Other%20Tools.md) says.
- **Browser and device clouds** (the main product of LambdaTest and BrowserStack) and hosted visual regression.
- **SCIM provisioning, SLAs and support plans.** They come with a company, not with code.
- **An IDE extension** (Currents). Later, if ever; the MCP server (G4) reaches editors first.

## Sources

Read 2026-10-31:
[ReportPortal SaaS pricing](https://reportportal.io/pricing/saas/), [features](https://reportportal.io/features/) and [quality gates](https://reportportal.io/docs/quality-gates/QualityGatePurpose/) ·
[Allure TestOps pricing](https://qameta.io/server-pricing/) ·
[Qualflare plans](https://docs-7ra.pages.dev/reference/plans-pricing) ·
[BuildPulse, from a comparison page](https://testdino.com/compare/buildpulse-vs-testdino) ·
[Currents pricing](https://currents.dev/pricing) ·
[Datadog Test Optimization, from a comparison page](https://testdino.com/blog/currents-vs-datadog) ·
[Trunk billing](https://docs.trunk.io/setup-and-administration/billing) ·
[TestMu AI (LambdaTest) pricing](https://www.testmuai.com/pricing.md) ·
[BrowserStack advanced features](https://www.browserstack.com/docs/test-reporting-and-analytics/features/advanced-features) and [tiers, from a comparison page](https://testdino.com/alternatives/best-browserstack-test-reporting-alternatives/) ·
[TestRail pricing](https://www.testrail.com/pricing/) ·
[Testmo pricing](https://www.testmo.com/pricing/) ·
[Buildkite pricing](https://buildkite.com/pricing/).
The comparison pages are written by a competitor (TestDino) and were used only for prices.
