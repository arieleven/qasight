---
type: guide
status: current
created: 2026-10-25
updated: 2026-11-04
---

# Compared with other tools

[Index](Index.md) · [Getting Started](Getting%20Started.md)

QASight keeps the history of your test runs and tells you what changed: what broke, what's flaky and what got slower. It does that for any runner that writes JUnit XML (or that it can run itself), as a `pip install` on a laptop or one container for a team, with no account and no per-seat bill. This page says where that fits next to the tools teams already know, and when one of them is the better choice.

Features and prices of the other tools change often. This page describes them as of October 2026, from their own docs; check theirs before you decide.

## At a glance

| | QASight | Allure Report | ReportPortal | Currents | Trunk Flaky Tests | Datadog Test Optimization |
|---|---|---|---|---|---|---|
| Hosting | Your machine or your server | Static files | Your server (several services) or their cloud | Their cloud | Their cloud | Their cloud |
| Price | Free, MIT | Free | Free self-hosted; paid cloud | Paid, by usage | Free tier, then paid | Paid, part of Datadog |
| Runners | Any that writes JUnit XML, plus pytest, Behave, BehaveX and Playwright adapters | Most, through Allure adapters | Most, through agents | Playwright, Cypress | Most, through JUnit upload | Most, through Datadog libraries |
| History across runs | Yes, with per-test diffs between any two runs | One run, with an optional history folder | Yes | Yes | Yes | Yes |
| Flaky test detection | Yes, plus quarantine from config, the UI or the CLI | Retries only | Yes | Yes | Yes | Yes |
| Per-test history page | Yes: outcomes, flake rate over time, duration, owner | Per run | Yes | Yes | Yes | Yes |
| Labels on tests (feature, severity, tags, issue links) | Yes, read from Allure results, Playwright tags and JUnit properties, with counts per label and an Issues card that shows which issues have red tests | Yes | Yes (attributes) | Tags | No | Tags |
| Traces, screenshots and videos | Playwright attachments on the test page | Yes, the richest per-run view | Yes | Yes, streamed live | Not its focus | Through Datadog APM |
| Runs the tests for you | Yes (`qasight run`, cycles of stages; only chosen or failed tests with `--test` / `--rerun-failed`) | No | No | Orchestrates Playwright and Cypress | No | No |
| Test case management and manual testing | No | No (Allure TestOps does) | No | No | No | No |

## When to pick something else

- **You want the richest report for a single run.** Allure Report's per-run view (steps, retries, attachments) is deeper than QASight's. They work together: QASight can write Allure reports as one of its reporters and link to them from each run.
- **You need test case management or manual testing.** That's Allure TestOps, Testmo and similar tools. QASight only knows about tests that ran.
- **You want failures sorted automatically, with ML, across a large organisation.** ReportPortal's auto-analysis learns from past decisions. QASight remembers a cause someone marked and applies it to failures with the same message, which is simpler and easier to predict, but it doesn't guess.
- **You run Playwright or Cypress at scale and want CI load balancing.** Currents splits and orders specs across machines and streams results live. QASight records what ran; it doesn't schedule specs across CI machines.
- **Your team already lives in Datadog.** Datadog Test Optimization links tests to traces, deploys and test impact analysis. QASight can send each run to Datadog as an OpenTelemetry trace ([`qasight otel`](CLI%20Commands/Command%20Reference.md#qasight-otel)) and pick the tests a change can affect ([`qasight affected`](CLI%20Commands/Command%20Reference.md#qasight-affected)), but it doesn't link test spans to your services' traces.

## When QASight fits

- Test data can't leave your network, or you don't want another SaaS and another per-seat bill.
- You use more than one runner (pytest, Playwright, Behave, a shell script) and want them in one history.
- You want "what changed since `main`" on every pull request without wiring up a hosted service: one CI step with the [GitHub Action](Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md), or `pytest --qasight` on your own machine.
- You want to try it in minutes: `pip install qasight`, `qasight ingest results.xml`, `qasight ui`. The [Getting Started](Getting%20Started.md) guide has the details, and the live demo's try-it page reads a JUnit file in your browser with nothing installed.
