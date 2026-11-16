---
type: guide
status: current
created: 2026-11-16
updated: 2026-11-16
---

# Moving from ReportPortal

[Index](../Index.md) · [Team Server](Team%20Server.md) · [Compared With Other Tools](../Compared%20With%20Other%20Tools.md)

A team on ReportPortal can move to a QASight [team server](Team%20Server.md) without touching its tests: point the ReportPortal agents it already uses at QASight, and import the launches and defect decisions it already has. One container and a database replace ReportPortal's services (API, UI, analyzer, OpenSearch, RabbitMQ, MinIO).

## 1. Start a team server

```bash
pip install qasight
qasight server init --domain qasight.example.com
docker compose up -d
```

The token in `.env` (`TESTO_API_TOKEN`) is the API key the agents will use. [Team Server](Team%20Server.md) has the details, accounts and HTTPS.

## 2. Point the agents at it

Change the endpoint and the key; the project can stay what it was (QASight doesn't need it). Each launch then shows under **Running now** while it runs, and becomes one run of the cycle named after the launch (`Smoke Tests` becomes the cycle `Smoke-Tests`) when it finishes.

pytest (`pytest-reportportal`), in `pytest.ini`:

```ini
[pytest]
rp_endpoint = https://qasight.example.com
rp_api_key = <TESTO_API_TOKEN>
rp_project = shop
rp_launch = Smoke Tests
rp_launch_attributes = branch:main env:staging
```

Playwright (`@reportportal/agent-js-playwright`), in `playwright.config.js`:

```js
reporter: [['@reportportal/agent-js-playwright', {
  endpoint: 'https://qasight.example.com/api/v1',
  apiKey: process.env.TESTO_API_TOKEN,
  project: 'shop',
  launch: 'Playwright UI',
  attributes: [{ key: 'branch', value: process.env.GITHUB_REF_NAME }],
  includeTestSteps: true,
}]],
```

Other agents (JUnit 5, TestNG, Cypress, WebdriverIO, .NET, Robot Framework) take the same three settings in their own config file: the endpoint (with `/api/v1` or `/api/v2` where that agent expects it in the URL), the key, and the project. Keep the key in a CI secret.

What carries over from a launch:

| ReportPortal | QASight |
|---|---|
| Launch | A run of the cycle named after the launch, recorded when the launch finishes |
| Suites (and nested suites) | The test's group, and `parentSuite`, `suite`, `subSuite` labels |
| Test (step with stats) | A test result, with its duration and status |
| Nested steps (`includeTestSteps`, `hasStats: false`) | Steps on the test page |
| Logs and attachments | The failure message (pytest's `E` line or the exception line), a `log` attachment, and the files (20 MB each, 200 MB per launch) |
| Attributes | Labels; `branch`, `commit` (or `sha`), `repository`, `env` and `release` also fill in the run's branch, commit, environment and release, and `tier` its tier |
| A retried test | One result, marked flaky when a retry passed |
| A launch nobody finished | Recorded anyway after 6 hours of silence |

Tested with `pytest-reportportal` 5.6 (which calls `/api/v2`) and `@reportportal/agent-js-playwright` 5.5 (`/api/v1`, with retries, steps and screenshots), on one worker and on three.

Not answered: ReportPortal's own UI and its API for dashboards, filters, defect updates, launch merges and imports. QASight's UI, CLI and API show the runs instead.

## 3. Import what you already have

```bash
export TESTO_RP_TOKEN=<a ReportPortal API key>
docker compose exec -e TESTO_RP_TOKEN server qasight import reportportal \
  --url https://rp.example.com --project shop --limit 1000
```

Each launch becomes a run dated when it ran, so trends, flaky tests and test history start full, and [`qasight case-study`](../CLI%20Commands/Command%20Reference.md#qasight-case-study) has a "before" period. Each defect type someone set becomes a marked cause:

| ReportPortal defect type | QASight cause |
|---|---|
| Product bug (`pb…`) | Product bug |
| Automation bug (`ab…`) | Bug in the test |
| System issue (`si…`) | Environment or infrastructure |
| No defect (`nd…`) | Other |
| To investigate (`ti…`) | Not marked |

Custom subtypes map by their parent type. Issues linked from ReportPortal's bug tracker go in the cause's note, as links. Running the import again skips launches already imported.

## 4. What replaces auto-analysis

ReportPortal's analyzer suggests a defect type from similar past failures. QASight does the same from the causes your team marked, imported ones included: a failure with no cause shows a suggestion, how sure it is, and the marked failure it looks like, and **Accept** marks it. A marked cause also applies at once to every later failure with the same message. To see how good the suggestions are on your own history:

```bash
qasight triage accuracy --cycle Smoke-Tests
```

It replays every marked cause as if it were new and prints how often the suggestion was right and how often there was one. Design: [Surpass ReportPortal - 2026-11-16](../Specs%20&%20ADRs/Surpass%20ReportPortal%20-%202026-11-16.md).

## 5. Projects, people and Jira

- ReportPortal projects map to cycles. Give each team a role on its own cycles with [`qasight users grant alice runner 'shop-*'`](Team%20Server.md#roles-per-cycle).
- Set the `TESTO_JIRA_*` variables to file a failing test in Jira from its run page ([Team Server § Jira](Team%20Server.md#jira)).
