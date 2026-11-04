---
type: spec
status: current
created: 2026-10-26
updated: 2026-11-04
---

# Project Health - 2026-10-26

## Context

QASight already knows how the project it tests is doing: pass rates, flaky tests, tests that got slower, skipped and quarantined tests, the test pyramid. Each fact sits on its own page (dashboard, cycle page, flaky card), and there was no single answer to "how healthy are this project's tests?".

An AI review was also asked for, with review "skills" the user can see, switch on and off and expand. A first draft modeled the skills on the reviews run on QASight itself (architecture, UX copy, product readiness, accessibility). That was judged out of scope: those judge the product and its code, which is what code review tools such as SonarQube or CodeRabbit do. QASight runs tests and explains their results, so the AI part was cut down to checks about the tests only.

## Decision

A **Project health** page (`/health`, in the main navigation) with two parts.

1. **Test health score** (`qasight/services/project_health.py`, `GET /api/v1/project-health?cycle=`). A 0-100 score and a school grade, the weighted mean of six signals that have data: passing tests (30%), green builds over each cycle's last 20 runs (20%), stable tests (20%, each 1% flaky costs 10 points), speed trend (10%, each 1% slower costs 5), test hygiene (10%, each 1% skipped or quarantined costs 3) and the test pyramid (10%). It reuses `test_history.list_cycle_tests` and the pyramid model, so it agrees with the cycle page. Same runs, same score: no AI. The page also lists what to fix first (red cycles, flaky, slower, quarantined, skipped tests, each linked) and a score per cycle. Code coverage is not measured by QASight, so it is not a signal.
2. **AI review** (`qasight/services/project_review.py`). Seven built-in checks (`GET /api/v1/project-health/skills`): why tests are flaky, related failures, test strategy gaps, test debt, slow tests, test churn, and test names and messages (off by default). Each has a one-line tagline, a short summary, what it checks and what it reads, shown when its row is expanded, and a switch remembered in the browser. `POST /api/v1/project-health/ai-review` sends the enabled checks' rubrics and an evidence block (scores, per-cycle results, redacted failure messages, flaky and slow tests, test names, tests that appeared or disappeared) to the configured provider in **one** call, and expects JSON with a 0-100 score, a summary and findings per check. The overall AI score is the mean of the checks that scored. It never reads source code, and the page labels it an AI estimate.

A review is stored in the newest run it looked at (`ai_project_review_v1` in the run's metadata, keyed by cycle or `*`), so it survives restarts on every database backend. `GET /api/v1/project-health/ai-review` returns it, `stale` when newer runs came in.

## Consequences

- One more page and four endpoints, all on the existing read model and AI provider. No new tables.
- The review needs a bigger answer than a run summary, so it raises the call's output tokens (450 per check, up to 4,000) and the timeout (at least 60 s), whatever AI settings say.
- A model that doesn't answer in JSON gives "unparseable_reply" and nothing is stored.
- The static demo exports the score, the checks and any stored review; running a review there is disabled like every other write.
- "Tests missing for recent changes" would need the changed files of each commit, which QASight doesn't record. It is left out for now.
