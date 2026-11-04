# QASight GitHub Action

This composite action is a thin wrapper around the CLI contract:

`qasight run [--config <path>] [--cycle <name>] --ci [--no-persist]`, or, with no
`qasight.yaml` in the workspace (or with `results`),
`qasight ingest [<results>...] [--cycle <name>] --ci`

It runs one cycle on the host runner (or records the results an earlier step
wrote), echoes the NDJSON event stream to the job log, and exposes the final
`plan_finished` event as step outputs.

## No config file

Keep your test step as it is, make it write results (JUnit XML, Jest/Vitest
`--json`, Playwright JSON, `go test -json`, `.trx`, ...), and add the action
after it. It finds the result files itself (by content; files git tracks are
skipped), records them under a cycle named after the repository, and comments
on the pull request:

```yaml
permissions:
  contents: read
  pull-requests: write
steps:
  - uses: actions/checkout@v4
  - run: npm ci && npx jest --ci --json --outputFile=test-results/jest.json
  - uses: arieleven/qasight@v1
    if: ${{ !cancelled() }}   # record failing runs too
```

## Inputs

- `results` (optional, default empty): result files to record instead of running a cycle
  (globs, space or newline separated). Empty with no config = find them
- `history` (optional, default `auto`): keep the run history (SQLite) in the Actions cache
  between runs, so each run is compared with the ones before it. Skipped when `DATABASE_URL`
  or the config's `database.url` is set; `off` never caches
- `config-path` (optional, default empty): path to `qasight.yaml`; empty means discovery
- `cycle` (optional, default empty): cycle name; empty runs the only cycle, `all` runs every
  cycle and publishes a summary, comment, annotations and alert for each
- `ci-mode` (optional, default `true`): NDJSON on stdout
- `persist` (optional, default `true`): write `plan_result.json` and the run history record
- `python-version` (optional, default `3.11`)
- `package` (optional, default `qasight`): what to pip install, e.g. `qasight==1.2.0`,
  `qasight[bdd]` when the cycle has `behave` or `behavex` stages, or several requirements
  separated by spaces (`qasight pytest-xdist`)
- `job-summary` (optional, default `true`): write the run to the job summary
- `comment-on-pr` (optional, default `auto`: on when recording results with no config, off
  when running a configured cycle): on `pull_request` events, keep one comment per
  cycle on the pull request with the same summary (edited on later pushes, not re-posted).
  Needs `permissions: pull-requests: write`; with a read-only token (fork PRs) it warns and moves on
- `annotations` (optional, default `true`): print failing tests as `::error` annotations
  (`qasight report annotations`), shown on the failing line in the pull request's "Files changed".
  Needs no permission
- `slack-webhook`, `teams-webhook` (optional, default empty): incoming-webhook URLs for a chat
  alert (`qasight notify`). Pass secrets, e.g. `${{ secrets.SLACK_WEBHOOK_URL }}`; empty sends nothing
- `notify-when` (optional, default `failure`): `failure` (red run or a newly flaky test),
  `change` (differs from the previous run) or `always`
- `flaky-issues` (optional, default `false`): open one issue (label `flaky-test`) for each test
  this run made flaky, unless an open issue already has its title. Needs `permissions: issues: write`
- `coverage` (optional): coverage report(s) your tests write, space-separated (Cobertura or
  JaCoCo XML, LCOV, Go cover profile). Line coverage is stored with the run, and the summary and
  PR comment show it and how much it moved against the baseline
- `server-url`, `server-token` (optional): also upload each recorded run to a
  [team server](https://arieleven.github.io/qasight/docs/processes-and-guides/team-server/)
  (`qasight ingest --server`), with the token from a secret (`${{ secrets.TESTO_API_TOKEN }}`).
  The summary and comment still come from the Actions-cache history; a failed upload is a warning
- `history-artifact` (optional, default `true`): with the cached history (`history: auto`), also
  upload the SQLite file as a `testo-history-<workflow>-<job>-...` artifact kept 7 days, so
  `qasight ui --from-github` opens the CI history on your machine. `false` skips it
- `static-site` (optional, default `false`): after the run, write the history as a read-only
  site (`qasight export-site`) and upload it for GitHub Pages, see
  [Results on GitHub Pages](#results-on-github-pages)
- `static-site-base-path` (optional, default: `/<repo>/`, or `/` for `<owner>.github.io`): the
  path the site is served under; `/` for a custom domain
- `ai-summary` (optional, default `false`): add an AI summary of the failures to a failed run's
  summary and comment (`qasight report markdown --ai`). Set the provider in the step's `env`:
  `TESTO_AI_PROVIDER` (`openai`, `anthropic`, `openai_compatible`), `TESTO_AI_MODEL`,
  `TESTO_AI_BASE_URL`, and the key from a secret (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or
  `TESTO_AI_KEY_ENV` naming another variable). A provider that doesn't answer is one line, not a failure
- `comment-artifact` (optional, default `false`): on `pull_request` events, upload the comment as
  a `testo-comment` artifact instead of posting it (output `comment_dir`), for pull requests from
  forks, whose token is read-only. A second workflow posts it with `post-comments-from`
- `post-comments-from` (optional): in a `workflow_run` workflow, only post the comments a
  `comment-artifact` run left in this folder, on the open pull request whose head is the commit
  that run tested (never a number from the artifact). Files must start with the
  `<!-- testo-summary:` marker; `@` mentions are defused. Runs nothing else.
  `qasight init --forks` writes both workflows; see [Pull requests from forks](#pull-requests-from-forks)
- `github-token` (optional, default `${{ github.token }}`): token for the comment and issues

The summary is `qasight report markdown`: pass/fail counts, a per-stage table, failing tests and
the tests that changed outcome since the cycle's previous run (for a pull request, the latest
run on its target branch). That history comes from the Actions cache (`history: auto`), or from
a `database.url` / team server shared between jobs.

## Outputs

- `exit_code` (`0`–`4`, see `docs/CLI Commands/Troubleshooting and Error Codes.md`)
- `status` (`success`, `failure`, `invalid_input`, `infra_failure`, `internal_error`)
- `summary_json`: the final `plan_finished` (or `error`) NDJSON event
- `summary_path`: the same JSON saved under `$RUNNER_TEMP/testo-summary.json`
- `comment_dir`: with `comment-artifact` on a pull request, the folder with the comment files

## Usage

```yaml
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: arieleven/qasight@v1
        with:
          cycle: sample-pytests
          comment-on-pr: "true"
          slack-webhook: ${{ secrets.SLACK_WEBHOOK_URL }}
```

with `permissions: { contents: read, pull-requests: write }` on the job for the comment
(add `issues: write` for `flaky-issues`).

## Results on GitHub Pages

`static-site: "true"` publishes the run history as a read-only copy of the web UI: the
dashboard, runs, tests, flaky tests and comparisons, at `https://<owner>.github.io/<repo>/`.
Turn on Settings → Pages → Source: **GitHub Actions**, then:

```yaml
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v7
      - run: pytest --junitxml=reports/junit.xml || true
      - uses: taltal-beep/testosterone@v1
        with:
          results: reports/*.xml
          static-site: "true"
  pages:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - id: deploy
        uses: actions/deploy-pages@v5
```

Publish from the default branch only: the site shows everything in the cached history. The
same folder comes from `qasight export-site` anywhere else (GitLab Pages, a bucket).

## Pull requests from forks

A fork's pull request gets a read-only `GITHUB_TOKEN`, so the comment can't be posted from its
run. Leave it as an artifact there and post it from a `workflow_run` workflow, which runs in
your repository with a token that can write and checks out no code from the fork:

```yaml
# .github/workflows/qasight.yml (the test workflow)
      - uses: arieleven/qasight@v1
        with:
          cycle: unit
          comment-on-pr: "true"
          comment-artifact: "true"
```

```yaml
# .github/workflows/qasight-comment.yml
name: QASight comment
on:
  workflow_run:
    workflows: [QASight]
    types: [completed]
permissions:
  actions: read
  pull-requests: write
jobs:
  comment:
    if: github.event.workflow_run.event == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        continue-on-error: true  # no artifact: nothing to comment
        with:
          name: testo-comment
          path: testo-comment
          run-id: ${{ github.event.workflow_run.id }}
          github-token: ${{ github.token }}
      - uses: arieleven/qasight@v1
        with:
          post-comments-from: testo-comment
```

`qasight init --forks` writes both.

## Migrating from the v1.0 `uqo` action

The v1.0 inputs `ghost-mode`, `stream-json`, `runner-image` and `runner-prebuilt`
drove the removed headless/Docker runner and are gone; `qasight run --ci` already
streams NDJSON and runs stages as host subprocesses. Use `Dockerfile.qasight-runner`
as the job container if you want a pinned image. The `run_id` output is gone too:
it read the old summary JSON, which `qasight run` does not emit.

## Versioning and pinning policy

- Publish immutable tags for every patch release: `v1.0.0`, `v1.0.1`, ...
- Keep moving major tag `v1` pointing to latest stable `v1.x`.
- For strict supply-chain policies, pin consumers to a commit SHA.

Inputs reach the action's shell steps as environment variables, never pasted into the script,
so an input such as `cycle: ${{ github.head_ref }}` can't run commands.
