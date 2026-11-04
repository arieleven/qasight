---
type: reference
status: current
created: 2026-10-24
updated: 2026-11-04
---

# Product review: scorecard and roadmap

[Index](Index.md) · companion to the code-side [Project Review](project-review.md)

The [Project Review](project-review.md) asked "is the code good?" and ended at 10. This note asks a different question: **would a team pick QASight over what they already use, and would they keep using it?** It looks at the product, not the code: positioning against the tools people actually adopt, the first ten minutes, the daily "why is CI red" workflow, the UI's design, accessibility and copy.

How the first pass was checked (2026-10-24, `main` at `9104054`):

- **Competitors:** Allure Report and Allure TestOps, ReportPortal, Currents, Trunk Flaky Tests, Datadog Test Optimization, Testmo and TestDino, from their own docs and comparison pages. Prices change often and some comparison pages are written by a competitor, so treat the figures as rough.
- **The demo UI:** the same static site that the [Pages demo](Processes%20&%20Guides/GitLab%20Pages%20Demo.md) publishes, built locally from `self-test` and `sample-all-frameworks` runs, plus the README screenshots of the failing fake-api runs. Every page was opened at 1280 px and 390 px.
- **Accessibility:** an automated `axe-core` 4.10 scan (WCAG 2 A/AA and best practice) of every page, keyboard tabbing through the shell, and a touch-target and horizontal-overflow check at 390 px. Automated scans catch about a third of real issues; no screen reader was used.
- **Copy:** the CLI's `--help`, the README, and the UI's headings, empty states and error messages.
- **Prior work:** the 2026-10-22 gaps review (ingest mode, team server, positioning, lean install, alerts, quarantine, Playwright, JSON Schema). All of those have shipped since, so this review starts where that one ended.

## Verdict

**Overall: 8.5 / 10 as a product (was 7.5), and 9 or more in every area except trust.** Third pass, 2026-10-25, `main` at `ed57b9d`, after the [Product to Ten](Specs%20%26%20ADRs/Product%20to%20Ten%20-%202026-10-25.md) work in #176 to #184. Every item in [What a 10 needs](#what-a-10-needs) that didn't wait on Taltal has shipped: recorded cycles have pages, "new" means new since the target branch, the dashboard leads with what needs attention, `pytest --qasight` records a local run with no CI step, and the test page has attachments, traces, flakiness over time and a remembered cause.

What keeps the areas from 10 is small and listed under [Third pass findings](#third-pass-findings): a cycle's own run history is hard to see (P15), the duration chart has no scale (D8), and three copy nits. What keeps the product from 10 overall is trust (one maintainer, nothing launched), which no pull request fixes.

How it was checked: a live API and UI built from `main`, with six ingested JUnit runs of a `web` cycle (a flaky test, a shared `ConnectionRefusedError`, a test that keeps getting slower), a Playwright JSON import with a screenshot, a video and a trace, and a `pytest --qasight` session on a branch. Every page opened at 1280 px, 390 px and 640 × 450 (200 % zoom); `axe-core` (WCAG 2 A/AA and best practice) on every page; triage marked and inherited in the browser. No screen reader, no users.

## Scorecard

| Area | Score | Was | In one line |
|------|:-----:|:---:|-------------|
| Positioning and value proposition | **9** | 8 | README, PyPI, `--help` and a [comparison page](Compared%20With%20Other%20Tools.md) that says when to pick something else. Left: the name and a Marketplace listing, both Taltal's |
| Time to first value | **9.5** | 7.5 | `pytest --qasight`, `qasight ingest` then `qasight ui`, a first-run guide on the empty dashboard, and a no-install try-it page. Every recorded cycle has a page |
| Core workflow: "why is CI red?" | **9** | 8 | New since the target branch, branch and commit on the run, rerun commands, Compare prefilled, causes remembered. Left: no way to see one cycle's runs over time (P15) |
| Feature depth against competitors | **9** | 8 | Triage memory, Playwright attachments and traces, flakiness over time. The rest of the gap (ML auto-analysis, CI load balancing, test impact analysis) is a product of its own, and 9 is where this plan said it would stop |
| Visual design and UX | **9** | 7.5 | The dashboard answers "what needs me?", the run and test pages read in the user's order. Left: the duration chart has no scale (D8), and the Runs list looks the same for every cycle (P15) |
| Accessibility | **9.5** | 8.5 | `axe` finds nothing on any page; no overflow at phone width or 200 % zoom; charts have text alternatives. Left: one screen-reader pass, which needs a person |
| UX copy | **9** | 8 | Plain sentences throughout. Left: C11 to C13 |
| Trust and adoption signals | **5** | 5 | Unchanged: one maintainer, no "used by", nothing launched. Only a launch and real users move it |

## What a 10 needs

Every area except trust, in the order that moves the score most. "Realistic" says where a 10 depends on something other than code. After the third pass, everything here has shipped except the screen-reader pass, the name and the Marketplace listing; what each area still lacks is in [Third pass findings](#third-pass-findings).

| Area | To reach 10 | Realistic |
|------|-------------|-----------|
| Time to first value | P10 (ingest cycles get pages), a first-run empty state in `qasight ui` with the `qasight ingest` command, and a pytest plugin (`pytest -p qasight` or `pytest --qasight`) that records every local run with no CI step | Yes |
| Core workflow | P11 to P14: "new since `main`" on the run page, branch and commit in its header, Compare prefilled with the run and its baseline, and a copyable "rerun these" command per runner (`pytest` node ids, `--grep` for Playwright) | Yes |
| Visual design | D7: a dashboard that answers "what needs me?" (failing cycles, new failures, new flaky tests, what got slower) above the numbers, and D3 run labels that differ | Yes |
| Accessibility | A8 and A9, a 200 % zoom pass, text alternatives for the bars, and one screen-reader pass (VoiceOver or NVDA, which needs a person) | Mostly; the screen-reader pass needs Taltal |
| UX copy | C8 and C10 | Yes |
| Feature depth | Triage memory (mark a failure's cause once, similar failures inherit it), Playwright traces, screenshots and videos on the test page, flake rate over time per test | 9 is realistic: Datadog's test impact analysis and Currents' CI load balancing are products in themselves |
| Positioning | The name decision, the Action on the GitHub Marketplace, and a "compared with" docs page | Needs Taltal's name call |

## Third pass findings

| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| P15 | Medium | **A cycle's own run history is hard to see.** Runs lists every cycle together with no filter, and a cycle's page shows its tests, flaky card and stages but not its runs or how often it has passed lately | A cycle filter on Runs (`/runs?cycle=web`) and a "Recent runs" card on the cycle page with the pass rate over those runs, linking to the filtered list |
| D8 | Low | The test page's duration chart has no scale: equal durations draw full-height bars, so a steady 300 ms test looks as busy as one swinging from 1 to 9 s | Label the chart's top value and draw bars from zero against it |
| C11 | Low | Slowdowns read "+1.1 s (+95.5%)" on the cycle page and "+1.1 s (+96%)" on the dashboard | One formatter for both (`formatPercent`) |
| C12 | Low | "0 of 3 cycles passed their latest run; 3 cycles are red." | "All 3 cycles are red." when none passed |
| C13 | Low | Runs says "Every archived cycle run, newest first." "Archived" is an internal word, and ingested runs aren't cycle runs to the reader | "Every recorded run, newest first." |

Second-pass findings P10 to P14, D7, A8, A9, C8 and C10 are fixed (#176 to #181). Third-pass findings P15, D8 and C11 to C13 are fixed since (see [Product to Ten](Specs%20%26%20ADRs/Product%20to%20Ten%20-%202026-10-25.md#third-pass-fixes-a-cycles-run-history-a-chart-scale-copy-p15-d8-c11-to-c13)).

## Second pass findings

### Second pass verdict (2026-10-25)

**Overall: 7.5 / 10 as a product (was 6.5).** Second pass, 2026-10-25, `main` at `ad4dc9f`, after the roadmap work in #166 to #174 ([Product Roadmap Work](Specs%20&%20ADRs/Product%20Roadmap%20Work%20-%202026-10-25.md)). The red build now lands: the run page leads with its failing tests, groups shared causes, says how long each has failed and links to a page per test, and PR comments compare with the target branch. The first look is three commands (`pip install`, `qasight ingest`, `qasight ui`) or no install at all on the try-it page.

It is 7.5 rather than the 8 the first pass predicted because of one new finding: **a cycle that only exists through `qasight ingest` has no cycle page** (P10). `GET /api/v1/cycles/{name}` reads `qasight.yaml`, so the cycle every ingest user creates is missing from Cycles and its page answers "not found", which hides the test search, the slowest tests and the flaky card from exactly the users the first look was rebuilt for. Fixing it is the single biggest step to 8.

How it was checked: the static demo rebuilt from `self-test`, `sample-all-frameworks` and three ingested JUnit runs of a `web` cycle; every page opened at 1280 px and 390 px; `axe-core` (WCAG 2 A/AA and best practice) on every page; keyboard tabbing through the shell; touch targets and overflow at 390 px; the API called directly for the ingest-only cycle. No screen reader, no users.

### Second pass scorecard

| Area | Score | Was | In one line |
|------|:-----:|:---:|-------------|
| Positioning and value proposition | **8** | 7.5 | README, PyPI and `qasight --help` say the same thing, and the demo has a try-it page. The name is still undecided and the Action isn't on the Marketplace |
| Time to first value | **7.5** | 6 | `qasight ui` from the wheel and a no-install try-it page. But an ingest-only cycle has no page (P10), and the first `qasight ui` with no runs doesn't show the one command to run |
| Core workflow: "why is CI red?" | **8** | 5.5 | Failing tests first, grouped causes, a page per test, PR comments against the target branch. The run page's "New" still means "since the previous run on any branch" (P11), shows no branch or commit (P12), Compare opens empty (P13) and there's no command to rerun what failed (P14) |
| Feature depth against competitors | **8** | 7.5 | Owners from CODEOWNERS, prefilled issues, per-test history, slowest and slowing tests. Still behind on triage memory (ReportPortal), traces and attachments (Currents, Allure) and flake-rate trends (Trunk) |
| Visual design and UX | **7.5** | 7 | The run page now reads in the user's order. The dashboard still doesn't: rollup chips with "0 unknown", blue icon tiles that carry nothing, a "Quick links" card, and health to two decimals (D7) |
| Accessibility | **8.5** | 7 | Skip link, focus ring, contrast, headings and reflow fixed; `axe` finds one moderate issue, the same on every page (A8). Left: the demo notice's 26 px close button and 16 px-high inline links (A9), and no screen reader or 200 % zoom pass |
| UX copy | **8** | 6.5 | Errors say what to check, sentence case, one ellipsis. Left: "Generate AI Summary", "Trend: regressed", "Irregular tier mix", "Non-ideal tier ordering", `20.00%` (C10), and the `gpt-4o-mini` default (C8) |
| Trust and adoption signals | **5** | 5 | Unchanged: one maintainer, no "used by", nothing launched. Only a launch and real users move it; out of scope for the code |

### Second pass table

| # | Severity | Finding | Fix |
|---|----------|---------|-----|
| P10 | High | **Ingest-only cycles have no page.** `GET /api/v1/cycles/web` answers 404 ("Unknown cycle") for a cycle created by `qasight ingest`, while `/analytics/tests?cycle=web` has its tests. Cycles doesn't list it and the static export skips it | List cycles from run history as well as config; a history-only cycle gets a read-only page (no Run panel) with its tests, flaky card and runs |
| P11 | Medium | **"New" on the run page is relative to the previous run on any branch.** `load_run_failures` walks the cycle's earlier runs regardless of branch, so on a busy repo "New" and "Failing for N runs" mix branches. #170 fixed this for the PR comment only | Use the same baseline as the PR comment (`pick_baseline`): earlier runs on the run's target or default branch, falling back to all runs; say which in the card |
| P12 | Low | The run page doesn't show the branch, commit or PR it ran for, though the run records them | A line under the title: branch, short commit (linked when `repository` is set) |
| P13 | Low | Compare opens with nothing selected, and same-minute runs read the same ("web · Oct 25 03:37" twice; D3 from the first pass) | Prefill the latest run and its baseline; label runs with seconds or a run number and branch |
| P14 | Low | No "rerun what failed" | A copy button on the failing-tests card with the runner's command for those tests |
| D7 | Medium | The dashboard is still data-model first (see Design and UX above); D6's icon tiles are unchanged | Lead with what needs attention; status-coloured or no icons |
| A8 | Minor | `axe` "region" on every page: the skip link sits before the sidebar, outside every landmark (it is the only content that does) | Put it inside a landmark (the `header`, or a `nav` labelled "Skip links") |
| A9 | Minor | Under the 24 px minimum target (WCAG 2.2 2.5.8): the demo notice's close button (26 × 26 px), inline "compare" and run links 15–18 px high in lists | 44 px close button; give list rows a taller hit area |
| C10 | Low | "Generate AI Summary", "Trend: regressed" / "Trend: flat", "Irregular tier mix", "Non-ideal tier ordering", health as `20.00%` | "Generate AI summary", "Down from 40%" / "No change", "Mostly unit tests", whole-number percentages |

First-pass findings P1 to P9, D1, D2, D4, D5, A1 to A7, C1 to C7 and C9 are fixed (#166 to #174). D3 and D6 are carried into P13 and D7, and C8 is still open.

Since the second pass, everything in [What a 10 needs](#what-a-10-needs) that doesn't wait on Taltal has shipped: #176 to #184, plus the [comparison page](Compared%20With%20Other%20Tools.md) (#185).

## First pass (2026-10-24)

### First pass verdict (2026-10-24)

**Overall: 6.5 / 10 as a product** (the code scores 10). The engineering is ahead of the product. Every capability a team needs is there: ingest from any runner, a shared server, flaky detection and quarantine, PR comments and annotations, Slack and Teams alerts, run-to-run diffs, AI summaries. But the two moments that decide adoption are weaker than the features behind them:

1. **The first look.** A `pip install` user has no UI. `qasight-api` serves one only when `TESTO_UI_DIR` points at a `frontend/dist` you built yourself from a checkout with npm, and [Getting Started](Getting%20Started.md) says the web UI "runs from a checkout of the repository". So the laptop story ends in the terminal, and the screenshots in the README show something a new user can't get without cloning the repo or running the Docker team server.
2. **The red build.** The question the README promises to answer is "what broke?". The run page doesn't answer it: it shows a run ID, `Type: cycle`, `Return code 1`, report links, a test pyramid that says *Healthy pyramid* on a failed run, and per-stage health. **It never lists the failing tests**, their error messages or how long they've been failing. You get there through Compare, the Allure report or the AI summary. And the PR comment compares with "the previous run of the cycle", which on a busy team is somebody else's branch, so "what changed" turns into noise.

Fix those two and the product score moves to about 8 without a new feature. After that, the gap to the paid tools is the per-test view (history, flake rate, duration trend, owner) and grouping failures by cause.

### First pass scorecard

| Area | Score | In one line |
|------|:-----:|-------------|
| Positioning and value proposition | **7.5** | The README and PyPI summary now lead with the outcome ("what broke, what's flaky and what got slower"), and "a `pip install` that keeps the history" is a real gap between Allure (one run) and ReportPortal (a server to run). The CLI's own tagline still says "unified quality orchestration CLI", and the name still costs search and workplace adoption (open decision from the gaps review) |
| Time to first value | **6** | `qasight ingest` is one CI step with no config, which is excellent. Seeing it in a UI needs a checkout and npm, or Docker. No hosted "try it with your JUnit file" either |
| Core workflow: "why is CI red?" | **5.5** | The run page has no failing-test list; the baseline is the previous run, not the main branch; there is no page for one test across runs; failures aren't grouped by message |
| Feature depth against competitors | **7.5** | Ahead of every free tool on history, diffs and flaky quarantine; behind Trunk and Datadog on per-test analytics, ownership and tickets; behind Currents on live per-test streaming and traces |
| Visual design and UX | **7** | Clean, consistent, responsive. Hierarchy follows the data model (summary, reports, pyramid, stages) instead of the user's question; prime space goes to IDs, a "Need help?" card and status chips; some numbers are noise (a 0.3 % change shown as a red *Regression*) |
| Accessibility | **7** | Few automated issues and a real `main` landmark, labelled controls and `aria-expanded` on most toggles. One page overflows at phone width, primary buttons fall just short of 4.5:1, small touch targets, no skip link, heading levels skip |
| UX copy | **6.5** | The demo notice and empty states are friendly. Errors say "Failed to load X." with no cause or next step; engineer words (`Return code`, `Type: cycle`, `Trend: unknown`, `state unknown`) reach the UI; title case, sentence case and two kinds of ellipsis are mixed; `--help` shows literal RST backticks |
| Trust and adoption signals | **5** | PyPI, Docker images, a docs site, a live demo, CI badges and a contributor. Still one maintainer, no "used by", no launch post (drafts exist, nothing posted) |

## Competitive position

| | What it is | Where QASight wins | Where it loses |
|---|---|---|---|
| **Allure Report** | Free HTML report for one run (with an optional history folder) | Real history and diffs across runs, a shared server, flaky detection; QASight can still produce Allure reports | Allure's per-test page (steps, attachments, retries) is far richer than anything in QASight's own UI |
| **Allure TestOps** | Paid test management with launches and dashboards | Free, small, `pip install`, no test-case management to adopt | Test cases, manual testing, custom dashboards, role-based views, SSO |
| **ReportPortal** | Open-source server with ML failure clustering; SaaS is enterprise-priced | One `pip install` or one `docker compose up` instead of a multi-service deployment | Auto-analysis that sorts failures into product bug, test bug or system issue, and remembers the decision; per-test history |
| **Currents** | Cloud dashboard for Playwright and Cypress, usage-priced (from about $49/month) | Any runner, self-hosted, free | Live streaming per test, traces and videos in the browser, CI orchestration and load balancing |
| **Trunk Flaky Tests** | Hosted flaky detection and quarantine (free tier, then per committer) | Self-hosted, free, also runs the tests and does diffs | Per-test pages, automatic quarantine, Jira and Linear tickets, "PRs rescued" metrics, test collections |
| **Datadog Test Optimization** | Test analytics inside Datadog (per committer) | No Datadog account, no per-seat bill | Flaky management at scale, test impact analysis, correlation with traces and deploys |

**The position to own:** *the free, self-hosted Trunk-and-Currents for teams that can't or won't send test data to a SaaS.* History, diffs, flaky quarantine and PR feedback for any runner, in one `pip install` or one container. It is already true on paper. The roadmap below makes it true in the first ten minutes and on the red-build page.

## First pass findings

Severity is product impact: **High** decides whether someone adopts or keeps the tool, **Medium** costs trust or time every day, **Low** is polish.

### Product and workflow

| # | Severity | Finding | Suggested fix | Size |
|---|----------|---------|---------------|:----:|
| P1 | High | **The run page doesn't show what failed.** `RunDetailPage` has Summary, Report Links, Test Pyramid, Stage Health, Artifacts and the AI summary, but no list of failing tests or their messages | Put a "Failing tests" card first on any failed run: test name, stage, first line of the message, "failing since" and a flaky badge, each opening the test's page (P4) | M |
| P2 | High | **No UI for `pip install` users.** The wheel doesn't contain the built frontend; `TESTO_UI_DIR` must point at a local `npm run build` | Build the frontend in the release workflow, ship it in the wheel and add `qasight ui` (open the browser on the local history). Already on the after-publish ideas list | M |
| P3 | High | **"What changed" compares with the previous run, not with main.** `run_summary_markdown` and the dashboard take the cycle's previous run as the baseline. With several PRs running, that's another branch | Record branch and commit on every run (ingest already sends them), and default the baseline to the latest run of the same cycle on the default branch, falling back to the previous run | M |
| P4 | Medium | **No page for one test.** You can't open a test and see its pass/fail history, flake rate, duration trend and last failure message | `/tests/:cycle/:id` page plus a test search box. It's the page every competitor builds first, and the flaky card and P1 can link to it | M |
| P5 | Medium | **Failures aren't grouped.** Ten tests failing with the same `ConnectionRefusedError` show as ten separate failures | Group failing tests by a normalised first line of the message ("3 failures: connection refused") on the run page and in the PR comment | S |
| P6 | Medium | **Performance deltas have no noise floor.** A +10 ms (+0.3 %) change in test time is a red *Regression* chip, so the Performance rollup is almost never clean | Treat changes under a threshold (for example 5 % and 100 ms) as unchanged, and say so in the [Delta Comparison Policy](Specs%20&%20ADRs/Delta%20Comparison%20Policy.md) | S |
| P7 | Medium | **No slowest-tests view.** "What got slower" is in the tagline, but only whole-run and per-stage durations are shown | A "Slowest tests" and "Got slower" list on the cycle page, from durations already recorded | S |
| P8 | Low | **Flaky tests stop at the list.** No owner (most repos already have a CODEOWNERS file), no "open an issue" link | Show the owner from CODEOWNERS and a prefilled GitHub issue link on each flaky test | S |
| P9 | Low | **No way to try it without installing.** The demo shows our data only | Later: a drop-a-JUnit-file page on the demo that renders it in the browser (no upload) | L |

### Design

| # | Severity | Finding | Suggested fix |
|---|----------|---------|---------------|
| D1 | Medium | The run page's top half is a run UUID, `Type`, `Return code`, wall time in ms and report links: data a human rarely needs first. The status, the failing tests and "what changed" should be above the fold | Order: status and one-line verdict ("5 failed, 3 new since main"), failing tests, what changed, then stages, pyramid and reports. Move the UUID into a copy button |
| D2 | Medium | Mixed signals: a failed run shows *Healthy pyramid* in green, and the header shows a green *All systems go* (it means the API's database is up) next to a red run | Rename the status dot to "API connected" (or show it only when something is wrong); call the pyramid "Test mix" and drop the health colour |
| D3 | Medium | Compare labels both runs "fake-api · Oct 22 04:00" when they ran in the same minute; the only difference is an 8-character hash | Show seconds or a run number (`#41` vs `#42`) and the branch |
| D4 | Low | Durations switch between `6429 ms` and `6.43 s` on neighbouring pages | One formatter: seconds above 1 s everywhere |
| D5 | Low | The "Need help? Please check our docs" card is the most colourful thing in the sidebar and sits on every page | A plain "Docs" link in the sidebar footer |
| D6 | Low | The dashboard's stat cards use large icon tiles that carry no information, and "Trend: unknown" when there's one run | Drop the icons or make them status-coloured; hide the trend line until there are two runs |

### Accessibility (WCAG 2.1 AA)

The automated scan found no critical issues. Every page has `lang`, a title, one `main`, labelled navigation and form controls. What it and the manual checks found:

| # | Severity | Issue | WCAG | Fix |
|---|----------|-------|------|-----|
| A1 | Major | **AI Settings overflows at phone width**: the form's selects and inputs are about 427 px wide in a 390 px viewport, so the page scrolls sideways (70 px) | 1.4.10 Reflow | `w-full min-w-0` on the form fields and card |
| A2 | Major | Primary buttons (white on `#0075ff`, 14 px) are **4.2:1**, under 4.5:1 | 1.4.3 Contrast | Darken the button to about `#0066e0` |
| A3 | Minor | Touch targets under 44 px on phones: menu button 36 × 36, notice close 26 × 26, inline links 15–18 px high, form controls 38 px | 2.5.5 Target size (AAA; 2.5.8 in WCAG 2.2 AA needs 24 px) | Pad the icon buttons to 44 px and give list links taller rows |
| A4 | Minor | No skip link; keyboard users tab through the demo notice and nine sidebar items before reaching the page | 2.4.1 Bypass blocks | A "Skip to content" link targeting `main` |
| A5 | Minor | Focus is the browser default 1 px outline on most links; only the active nav item has a ring. It's visible, but faint on the dark navy | 2.4.7 Focus visible | One `focus-visible:ring-2` style for links and buttons |
| A6 | Minor | Card titles are `h3` straight after the page's `h1` (no `h2`), and AI Settings has no `h1` | 1.3.1 Info and relationships | Cards use `h2`; give AI Settings an `h1` |
| A7 | Minor | The run page's "Artifacts" toggle has no `aria-expanded` (the other toggles do), and the header status text sits outside any landmark | 4.1.2 Name, role, value | Add `aria-expanded`; put the header bar in a `header` landmark |

Not tested: screen reader output (VoiceOver, NVDA), 200 % zoom, and the charts' text alternatives.

### UX copy

| # | Where | Now | Suggested |
|---|-------|-----|-----------|
| C1 | `qasight --help` | "QASight — unified quality orchestration CLI." | "See what broke, what's flaky and what got slower on every run." (match the README and PyPI) |
| C2 | `qasight --help` | Command names such as `qasight report native` and `--cycle` print wrapped in literal double backticks; "qasight.yaml (or pyproject )" has a stray space | Plain text or single quotes; fix the space |
| C3 | Error states | "Failed to load runs." / "Failed to load delta comparison." / "Failed to load AI settings." | Say why and what to do: "Couldn't reach the API at `…`. Is `qasight-api` running?" with a Retry button |
| C4 | Run page | `Type: cycle`, `Return code: 1`, subtitle "Run Details" | Drop Type; "Exit code 1 (tests failed)"; drop the subtitle |
| C5 | Dashboard | "Trend: unknown", "Allure report (state unknown)", "Behave report (not available)" | Hide what isn't known; list only reports that exist |
| C6 | Everywhere | "AI Integration Settings", "Pass Count", "Report Links" next to "Bring your own key", "Healthy pyramid balance" | Sentence case for every heading and label ("AI settings", "Passed", "Reports") |
| C7 | Loading states | "Loading cycles…" and "Loading AI summary..." | One ellipsis character everywhere |
| C8 | AI Settings | Default model `gpt-4o-mini` | A current default per provider, or leave the field empty with a placeholder |
| C9 | Sidebar | "ADVANCED: Quick Run, AI Settings" | "Tools" or just "Settings"; Quick Run is the easiest way to try a run, not an advanced one |

## Roadmap: Now / Next / Later

Ordered by what moves adoption most for the least work. Sizes: S is a day or less, M a few days, L a week or more. All of Now and Next, and owners, tickets and the try-it page from Later, shipped in #166 to #174 (1.3.0, the try-it page after it). Triage memory and traces shipped since, in #183 and #184. Left: the launch and the name; [What a 10 needs](#what-a-10-needs) is the current plan.

### Now: make the first look and the red build land (about a week)

| Item | Findings | Size | Why now |
|------|----------|:----:|---------|
| **Failing tests first on the run page** | P1, P5, D1, D2, C4 | M | It's the promise in the tagline, and every visitor to the demo lands on a failed fake-api run |
| **Ship the UI in the wheel: `qasight ui`** | P2 | M | Turns "install, ingest, look" into three commands with no checkout; makes the README screenshots true for a `pip install` user |
| **Noise floor for performance deltas** | P6 | S | Stops the dashboard and Compare crying wolf on every run |
| **Accessibility and copy pass** | A1–A7, C1–C9, D3–D6 | S | Mostly class and string changes; fixes the one real reflow bug and the CLI's out-of-date tagline |

### Next: be the tool a team keeps (2 to 4 weeks)

| Item | Findings | Size | Why next |
|------|----------|:----:|---------|
| **Main-branch baseline** | P3 | M | Makes the PR comment and Compare trustworthy on a team with parallel PRs; the data (branch, commit) is mostly recorded already |
| **Per-test page and test search** | P4 | M | The page every competitor builds first; the failing-tests card, flaky card and PR comment all link to it |
| **Slowest and slowing tests** | P7 | S | Delivers the "what got slower" third of the tagline |
| **Launch** | Trust | S | Post the drafts that already exist (Show HN, r/Python, r/QualityAssurance, awesome lists) once the Now items are live in the demo, so the first impression is the fixed one |

### Later: depth and reach

| Item | Findings | Size | Notes |
|------|----------|:----:|-------|
| **Failure triage memory** | | L | Mark a failure as product bug, test bug or environment once, and have similar failures inherit it (ReportPortal's strongest feature, done with plain message matching before any ML) |
| **Owners and tickets for flaky tests** | P8 | S–M | CODEOWNERS for owner, a prefilled GitHub issue; Jira and Linear through the plugin API |
| **Try-it page on the demo** | P9 | L | Drop a JUnit file and see it rendered, entirely in the browser |
| **Traces and attachments in the UI** | | M | Playwright traces, screenshots and videos on the per-test page, the main thing Currents and Allure do better |
| **Name decision** | Trust | S to decide | Keep "QASight", or lead with "Testo" everywhere. Still Taltal's call; it only gets more expensive after the launch |

## What this review does not cover

No user interviews or usage data exist yet, so every finding is an expert review, not evidence of what users do. The first real signal will come from the launch: watch which page people land on, which install path they take, and which issues they open, then revisit this note.
