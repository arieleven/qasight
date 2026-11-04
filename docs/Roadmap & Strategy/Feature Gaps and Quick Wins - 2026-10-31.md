---
type: roadmap
status: current
created: 2026-10-31
updated: 2026-11-04
---

# Feature gaps and quick wins (2026-10-31)

[Index](../Index.md) · [Product Roadmap](Product%20Roadmap.md) · [Product Review](../product-review.md) · [Compared With Other Tools](../Compared%20With%20Other%20Tools.md)

A market pass and a new-user pass, done together on 2026-10-31 against `qasight` 1.4.0 from PyPI and `main` at `6f40902`. It asks two questions: **what do teams get from the paid tools that QASight doesn't do yet**, and **what goes wrong in the first ten minutes**. Nothing here is built yet except where a pull request is named. Big items are proposals that wait on the maintainer's go-ahead.

Effort is for one person who knows the code: **S** is under a day, **M** is one to three days, **L** is one to two weeks, **XL** is more.

## How it was checked

- **Competitors**, from their own docs, pricing pages and changelogs, plus user complaints where they could be found (GitHub issues, G2, Capterra, Ministry of Testing, GitHub community discussions): ReportPortal, Allure TestOps and Allure Report 3, Currents, Testmo, Trunk Flaky Tests, BuildPulse, Datadog Test Optimization, CloudBees Smart Tests (Launchable), Qualflare, Buildkite Test Engine, Develocity, and the test reports built into GitLab and GitHub. Reddit couldn't be read and independent reviews were thin, so most complaints come from issue trackers. Pages written by a competitor were treated as one-sided. Prices change often; treat them as rough.
- **A brand new user's first ten minutes:** a fresh Python 3.13 virtualenv, `pip install qasight pytest` (1.4.0, about 10 s), `pytest --qasight` six times on a project with a flaky test and a slow test, `qasight summary`, `qasight flaky`, `qasight config init`, `qasight run`, `qasight report`, `pip install 'qasight[api]'`, `qasight ui`, then every page of the UI opened in a headless browser. Plus `qasight ingest` of hand-written JUnit files: Surefire's `<flakyFailure>`, a retried test written twice (the Gradle test-retry plugin's shape), nested `<testsuite>` elements, and a 1.1 MB file with 20,000 tests.

## What the market treats as standard in 2026

Every paid flaky-test tool now does most of these. The left column is what users have come to expect; the right column is where QASight stands.

| Expected | Who does it | QASight today |
|---|---|---|
| A test that fails and then passes **on a retry in the same run** is flaky | All of them (Datadog, Trunk, Develocity, Buildkite, Currents) | Every importer that sees retries (JUnit, Surefire, Playwright, Jest, Xcode) marks them, and `qasight flaky` counts one as flaky (Q2, Q3, G1, built 2026-11-01) |
| Flakiness judged on the default branch or the same commit, not across feature branches | Datadog, Trunk, GitLab ("failed n times in main") | Flips counted per branch, and passing and failing on one commit is flaky (G1, built 2026-11-01) |
| **Flaky** kept apart from **broken** (fails nearly every time) | Trunk | Separated: failing each of the last 3 runs is broken (G1, built 2026-11-01) |
| Quarantine enforced at run time, set from the UI with no code change, "mute" (runs, doesn't fail the build) rather than skip | Trunk, Buildkite, Datadog, Currents, Allure TestOps | A Quarantine button on the flaky list and the test page, `qasight quarantine`, and a list the server keeps, applied to `qasight run`, `qasight ingest` and `pytest --qasight`; released after N clean runs (built 2026-11-01, G2) |
| A ticket opened when a test turns flaky or is quarantined, assigned to its CODEOWNERS owner, closed when fixed | Trunk, BuildPulse, Datadog, Allure TestOps | GitHub issues opened, assigned and closed for flaky tests (`TESTO_ISSUES=github`, built 2026-11-01); not yet for quarantined tests, Jira or Linear (G3) |
| An MCP server that lets coding agents read failures and act (quarantine, retry) | Datadog, Trunk, BuildPulse, Currents, Allure TestOps, Buildkite | `qasight mcp`: read tools and `mark_failure_cause`; no quarantine or retry tool yet (G4) |
| Failures grouped by error signature, with a root-cause category | ReportPortal, Allure TestOps, Trunk, Datadog, Launchable, Qualflare | Shared causes grouped on one run; triage memory matches the same message. No cross-run clusters or categories (G5, G6) |
| Flaky tests ranked by cost (CI time wasted, PRs blocked) | BuildPulse, Trunk | Ranked by flip rate (G7) |
| Quality gates that set the exit code (pass rate, new failures, new tests, duration) | Allure Report 3, ReportPortal (paid) | `--fail-on-flaky` only (G8) |
| A retention policy, with a "keep this run" pin | ReportPortal, Currents, Buildkite | History grows forever (G9) |
| Sharded CI jobs merged into one run | ReportPortal (launch merge), Currents | Each `qasight ingest` is its own run (G10) |
| SSO (OIDC or SAML) and per-project roles | ReportPortal, Currents (paid), Testmo (paid), Allure TestOps | Tokens, or local accounts with roles (G11) |

## Where QASight already wins

Worth saying in the README and on the comparison page, because the complaints about the others are mostly here:

- **Self-hosted and free.** Of the tools above only ReportPortal (free, but heavy) and Develocity (paid, build-tool only) self-host; Trunk's on-prem is a private preview, Currents self-hosts only on Enterprise, and Testmo is cloud only.
- **Light.** One container and Postgres, against ReportPortal's documented minimum of 4 CPU, 8 GB of RAM and 300 GB of disk, with OpenSearch and RabbitMQ. ReportPortal's issue tracker has upgrades that lose projects or fail to migrate.
- **Long history at no extra cost.** Buildkite keeps 7 days of history on Pro and 28 on Enterprise; Currents keeps results for 90 days; GitLab's "failed recently" counts 14 days.
- **No per-test or per-committer bill.** Users complain that Buildkite's per-test pricing discourages writing tests; Launchable lists $15,000 per suite per year.
- **Robust import.** ReportPortal has open issues for a 1.3 MB JUnit file timing out and for nested `<testsuite>` elements failing. Here a 1.1 MB file with 20,000 tests ingested in 8 s and nested suites worked.
- **No tracer.** Datadog's tracer-based collection has bugs where it patches the HTTP client under test (dd-trace-py #20584) or ignores quarantine (dd-trace-js #10647); QASight only reads result files.
- **GitHub has no tests tab.** A June 2025 request for one has 39 votes and no answer from GitHub ([community discussion #163123](https://github.com/orgs/community/discussions/163123)); the Action's job summary and pull request comment fill that gap today.

## Quick wins

Small, mostly found in the first ten minutes. Ordered by how many new users hit them.

| # | Found | Effort | Fix |
|---|---|:---:|---|
| Q1 | **`qasight` with no arguments, `qasight runn` or `qasight run --bogus` prints a Python traceback** with Typer 0.2x, which a fresh install gets. The first thing many people type | S | **Fixed in #213**: the error and a hint (or the help), exit `2` |
| Q2 | **A test retried in the same JUnit file (Gradle's test-retry plugin writes it twice, failed then passed) fails the run** and counts as two tests: `qasight ingest` exits `1` for a build the CI called green | S–M | Collapse duplicate test cases to the last outcome; one that failed and then passed is "passed, flaky" |
| Q3 | Maven Surefire's `<flakyFailure>` and `<rerunFailure>` are ignored: the test passes and the retry is lost | S | Count them as flaky like Playwright's retries |
| Q4 | **Run pages say "health 100%"** for a run's pass rate (Runs list, the stage bar's screen-reader label, Compare's "Per-stage health", the pull request comment), while the dashboard's "Project health" is a different 0–100 score | S | "100% passed" / "pass rate" everywhere a run is meant |
| Q5 | Compare shows "0 regressions · 0 improvements · **9 unchanged**" for a 4-test run: the 9 are metrics, not tests | S | Count tests, or label it "9 metrics unchanged" |
| Q6 | ~~The stage name and its equipment run together: "pytestjunit"~~ Not a bug: they are separate on screen, only copied text runs them together | – | None |
| Q7 | **"Not a clean pyramid: each tier should be smaller than the one below it"** for a project with 4 unit tests and no others | S | Ignore empty tiers; say nothing when there's only one |
| Q8 | `qasight flaky` on a `pytest --qasight` or `qasight ingest` cycle says to paste a `quarantine:` snippet into `qasight.yaml`, which those users don't have | S | **Fixed 2026-11-01**: it suggests `qasight quarantine add --cycle C "<name>"` (no config needed) and still shows the snippet for config users ([Quarantine From the UI - 2026-11-01](../Specs%20&%20ADRs/Quarantine%20From%20the%20UI%20-%202026-11-01.md)) |
| Q9 | `qasight report` without the Allure CLI: "Post-Workout Review … see your gains", and an error with reStructuredText backticks (` ``allure generate`` `) | S | Plain words, single backticks |
| Q10 | Internal words in CLI output: "Archived cycle report &lt;uuid&gt;" after every run, "(Allure Σ (per-test))" and "No per-test differences detected (or no *-result.json in archives)" in `qasight summary` | S | "Recorded run 6c8e2369 (qasight ui to see it)"; drop the Allure wording |
| Q11 | Dashboard: "All 1 cycle passed their latest run." | S | "Your cycle passed its latest run." |
| Q12 | A `pytest --qasight` cycle shows its equipment as "junit" on Cycles and the run page | S | Record the runner as `pytest` |
| Q13 | Getting Started says it was tested with 1.1.1 and that `qasight ui` needs "releases after 1.2.0; before that it runs from a checkout" | S | Say 1.4.0; drop the old caveat |
| Q14 | The README's Quickstart starts at line 96, after a long "What's new". A visitor scrolls past three releases of changes before seeing `pip install` | S | Three commands right under the screenshots; "What's new" lower or in the changelog |
| Q15 | Flaky detection scans the newest 500 runs of **all** cycles and then filters to one, so on a busy server a nightly cycle can show fewer runs than its window | S | Ask the history for that cycle's runs |
| Q16 | `qasight run` prints two summary tables that say nearly the same thing (stage status and duration, then stage counts) | S | One table with both |

**Status:** Q1 is fixed in #213, Q3 in #232 and Q2 in #239. Q4, Q5, Q7, Q9 (the backticks and the error; the gym jokes stay), Q10, Q11, Q13 and Q14 are fixed in #234. Still open: Q8 (belongs with G2), Q12, Q15 and Q16.

## Feature gaps, ranked

Ranked by how much each moves adoption, against what it costs. Each is a proposal; the maintainer picks.

| Rank | Gap | Why it matters | Effort |
|:---:|---|---|:---:|
| G1 | **Better flaky signal.** _Built 2026-11-01 ([Better Flaky Signal - 2026-11-01](../Specs%20&%20ADRs/Better%20Flaky%20Signal%20-%202026-11-01.md))._ Retries in one run count as flaky (Q2, Q3, and pytest-rerunfailures' output); flakiness counted on the target branch, or as "different outcomes on the same commit", not across feature branches; flaky kept apart from broken | Flaky detection is the reason most teams look at these tools, and a false "flaky" costs trust fast. On a team server today a test a pull request broke and then fixed reads as flaky | M |
| G2 | **Quarantine from the UI, enforced without a config change.** _Built 2026-11-01 ([Quarantine From the UI - 2026-11-01](../Specs%20&%20ADRs/Quarantine%20From%20the%20UI%20-%202026-11-01.md)); a quarantined test doesn't open an issue yet (G3)._ A "Quarantine" button on the test page; the server keeps the list; `qasight ingest`, `pytest --qasight` and the Action read it, so a quarantined failure doesn't fail the job. Mute, not skip, so recovery shows; un-quarantine on its own after N green days | Trunk, Buildkite and Datadog all sell exactly this. The config-file route doesn't reach ingest or pytest users | M–L |
| G3 | **Issues for flaky and quarantined tests.** _Built 2026-11-01 for GitHub and flaky tests ([Flaky Test Issues - 2026-11-01](../Specs%20&%20ADRs/Flaky%20Test%20Issues%20-%202026-11-01.md)); quarantined tests, Jira and Linear are still open._ Open a GitHub issue (then Jira and Linear through plugins) when a test turns flaky or is quarantined, assigned from CODEOWNERS (already read), closed when the test is fixed, with no duplicates | The step from "we know it's flaky" to "someone owns it". Every paid tool has it; GitHub users ask for it in the tests-tab discussion | M |
| G4 | **MCP server.** Built 2026-11-01: `qasight mcp` over stdio with the read tools and `mark_failure_cause` behind `--allow-writes` ([MCP Server](../Specs%20&%20ADRs/MCP%20Server%20-%202026-11-01.md)). Left: quarantine and retry tools, HTTP transport and a team-server mode | Became standard in 2026 (Datadog, Trunk, Currents, Allure TestOps, Buildkite) | Done (read and mark cause) |
| G5 | **Failure clusters across runs.** Fingerprint each failure (message with numbers, paths and ids stripped, plus the top stack frame); show "this error, 14 tests, 6 runs, first seen …" on the dashboard and the cycle page. Triage memory then keys on the fingerprint | "One cause, many red tests" is the core of ReportPortal's and Datadog's analysis, and the deterministic version is easy to explain | M |
| G6 | **Cause categories.** Product bug, test bug, environment, flaky, or your own, on top of triage memory; filters and counts by category | ReportPortal's defect types are its most-used feature; it gives managers one chart | S–M |
| G7 | **Rank flaky tests by cost.** CI minutes lost (reruns times duration), runs and pull requests it made red | BuildPulse and Trunk lead with this; it decides what to fix first | S |
| G8 | **Quality gates.** `gates:` per cycle (minimum pass rate, no new failures against the target branch, maximum duration growth, minimum test count) that set the exit code and show in the pull request comment | Allure Report 3 has a whole rule set; teams use it to block merges | M |
| G9 | **Retention.** `TESTO_RETENTION_DAYS` or a per-cycle `keep:`, a pin to keep a run forever, and `qasight prune` | Without it a busy team server fills its disk; every hosted tool has it | S–M |
| G10 | **Merge sharded jobs into one run.** `qasight ingest --run-group "$GITHUB_RUN_ID"` (or the CI provenance already sent) so eight shards make one run in history | Sharded CI is common, and eight runs per pipeline also confuses flaky counts and Compare | M |
| G11 | **Sign-in with GitHub or any OIDC provider** for the team server, mapping groups to the existing viewer, runner and admin roles | The usual blocker for a team server inside a company. Currents and Testmo keep SSO behind their top plans | M–L |
| G12 | **Early flake detection for new tests.** In a pull request, run tests that are new since the target branch several times and flag the ones that don't agree | Datadog's version stops flaky tests from ever reaching `main` | M |
| G13 | **Generic webhooks** (`run.completed`, `test.flaky`, `test.fixed`, `test.quarantined`) next to Slack and Teams | Lets people wire QASight into anything without a plugin | S |
| G14 | **A GitHub check run with annotations** on the failing test lines, from the Action | Puts failures in the "Files changed" view. Mind GitHub's limits (50 annotations per step, 64 KB summary) | M |

## Out of scope, on purpose

- **Test case management and manual testing** (Allure TestOps, Testmo, and ReportPortal from 26.1). A different product, as the [comparison page](../Compared%20With%20Other%20Tools.md) already says.
- **ML auto-analysis.** ReportPortal's version needs OpenSearch and a model per project. G5 and G6 get most of the value and stay predictable.
- **CI load balancing** (Currents). `qasight split` covers the static half.
- **AI fix pull requests** (Trunk with Cursor, Datadog's Bits AI). Watch it; G4 lets users' own agents do it first.

## Suggested order

1. Quick wins Q2 to Q16 in one or two small pull requests (about two days in all). Q2 and Q3 change what a run's result is, so they get a CHANGELOG line.
2. G1 and G7: make "flaky" right before building on it.
3. G2 and G3 together: quarantine and ownership are one workflow.
4. G4 (read-only MCP, built 2026-11-01), then G5 and G6.
5. G9 and G10 before anyone runs a busy team server; G8, G11 to G14 as users ask.

## Sources

Competitor docs and pricing, read 2026-10-31:
[Trunk Flaky Tests](https://docs.trunk.io/flaky-tests/overview) and its [billing](https://docs.trunk.io/setup-and-administration/billing) ·
[BuildPulse](https://buildpulse.io/) ·
Datadog [Test Health](https://docs.datadoghq.com/tests/test_health/) and [flaky management](https://docs.datadoghq.com/tests/flaky_management/) ·
[CloudBees Smart Tests on AWS Marketplace](https://aws.amazon.com/marketplace/pp/prodview-mi3tgu7krlo5a) ·
Buildkite [quarantine](https://buildkite.com/docs/test-engine/quarantine) and [pricing](https://buildkite.com/pricing/) ·
[Develocity flaky test guide](https://docs.develocity.ai/2026.3/guides/flaky-test-detection-guide/) ·
GitLab [epic 9183](https://gitlab.com/groups/gitlab-org/-/work_items/9183) ·
ReportPortal [features](https://reportportal.io/features/), [auto-analysis](https://reportportal.io/docs/analysis/AutoAnalysisOfLaunches/), [retention](https://reportportal.io/docs/configuration/LaunchesRetentionPolicy/), [hardware](https://www.reportportal.io/docs/installation-steps/HardwareRequirements/) and [SaaS pricing](https://reportportal.io/pricing/saas/) ·
Allure TestOps [defects](https://docs.qameta.io/use-testops/results-and-analytics/defects/), [Allure 3](https://allurereport.org/docs/v3/) and its [quality gate](https://allurereport.org/docs/quality-gate/) ·
[Currents pricing](https://currents.dev/pricing) and [Actions](https://currents.dev/changelog/discoverability-for-currents-actions) ·
[Testmo pricing](https://www.testmo.com/pricing/).

User complaints:
[Buildkite forum on per-test pricing](https://forum.buildkite.community/t/unhappy-with-test-suite-pricing/4572) ·
[dd-trace-py #20584](https://github.com/DataDog/dd-trace-py/issues/20584) ·
[dd-trace-js #10647](https://github.com/DataDog/dd-trace-js/issues/10647) ·
[ReportPortal issues](https://issues.ecosyste.ms/hosts/GitHub/repositories/reportportal%2Freportportal/issues?page=1) (#2474 import timeout, #2434 nested suites, #2451 upgrade) ·
[Allure TestOps reviews on Capterra](https://www.capterra.com/p/227555/Allure-TestOps/reviews/) ·
[Currents feature board](https://currents.featurebase.app/) ·
[GitHub tests tab request](https://github.com/orgs/community/discussions/163123).
