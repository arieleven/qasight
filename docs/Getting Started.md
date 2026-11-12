---
type: guide
status: current
created: 2026-10-19
updated: 2026-11-12
---

# Getting Started

Add QASight to a project you already have, in about five minutes: install it, describe your tests in one file, run them, see what changed, then run the same thing in GitHub Actions. The commands below were checked against `qasight` 1.4.0 from PyPI in a fresh virtualenv.

You need Python 3.11 or newer and a project with tests. The examples use pytest; other runners are at [step 6](#6-add-other-test-runners).

**Start here:** `pip install qasight && qasight init` does steps 2, 3 and 5 for you: it detects your test runners (pytest, Jest, Vitest, Playwright, Go, .NET, Maven, Gradle, RSpec, PHPUnit), writes `qasight.yaml` and `.github/workflows/qasight.yml`, and records a first run. `qasight init --dry-run` shows the files first, and `qasight init --pr` also commits them on a new branch and opens the pull request. Tests in several folders (a monorepo)? `qasight init --scan --check` finds each one and checks it with its runner. Details: [Command Reference § qasight init](CLI%20Commands/Command%20Reference.md#qasight-init).

**No terminal? Set it up from GitHub.** Commit the workflow below as `.github/workflows/qasight-setup.yml`. It runs once, finds your tests (`qasight init --scan --attach`) and opens a pull request with the config and the change to your CI; nothing else happens until you merge it. On the docs site, type your repository and the button opens GitHub's editor with the file filled in:

<div class="qasight-setup" markdown="0">
<input id="qs-repo" placeholder="owner/repository" aria-label="Repository (owner/name)" size="28">
<input id="qs-branch" value="main" aria-label="Default branch" size="8">
<button type="button" onclick="qasightSetup()">Open in GitHub</button>
</div>
<script>
function qasightSetup() {
  var repo = document.getElementById("qs-repo").value.trim().replace(/^https:\/\/github\.com\//, "").replace(/\/$/, "");
  var branch = document.getElementById("qs-branch").value.trim() || "main";
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) { document.getElementById("qs-repo").focus(); return; }
  var text = document.querySelector("#qasight-setup-workflow code").innerText;
  window.open("https://github.com/" + repo + "/new/" + encodeURIComponent(branch) +
    "?filename=.github/workflows/qasight-setup.yml&value=" + encodeURIComponent(text), "_blank", "noopener");
}
</script>

<div id="qasight-setup-workflow" markdown="1">

```yaml
# Sets QASight up: finds your tests and opens a pull request that adds it.
# Runs when this file is committed (or from the Actions tab); delete it afterwards.
# Docs: https://arieleven.github.io/qasight/docs/getting-started/
name: QASight setup
on:
  workflow_dispatch:
  push:
    paths: [".github/workflows/qasight-setup.yml"]
permissions:
  contents: write  # push the qasight-setup branch
  pull-requests: write  # open the pull request
jobs:
  setup:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: arieleven/qasight@v1
        with:
          setup: "true"
```

</div>

GitHub doesn't let a pull request opened by Actions start your other workflows; push any commit to it (or close and reopen it) to run them. If your repository doesn't let Actions open pull requests (Settings, Actions, General), the step prints a link that opens it in one click.

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install qasight pytest
qasight --version          # qasight 1.x.y
```

Not a Python project? Run it without a virtualenv: `uvx --from qasight qasight init` (or `pipx run --spec qasight qasight init`). The UI needs the `api` extra: `uvx --from 'qasight[api]' qasight ui`.

No config yet? `qasight run -- pytest -q` (or `-- npm test`, `-- go test ./...`) runs your usual command and records it; see [Command Reference § Any test command](CLI%20Commands/Command%20Reference.md#any-test-command-no-config).

`qasight` is the `qasight` CLI. It doesn't install your test runner, so keep installing pytest (or Behave, Jest...) the way your project already does.

## 2. Describe your tests

Create `qasight.yaml` in the project root. A **cycle** is a named list of **stages**; each stage runs one test command.

```yaml
version: 1
cycles:
  unit:
    description: My unit tests.
    stages:
      - name: unit
        equipment: pytest
        args: [-q, tests]
```

Check it:

```bash
qasight config validate    # ok — version=1 cycles=1 ...
qasight doctor             # config, runners on PATH, database
```

For autocomplete in VS Code and other editors, put the [`$schema` line](CLI%20Commands/Command%20Reference.md#editor-autocomplete) at the top of the file.

`qasight config init` writes a starter file with a `smoke-test` cycle that runs `pytest -q tests`, plus commented-out marker-based pytest and BehaveX examples. Edit its `args` if your tests live outside `tests/`.

## 3. Run it

```bash
qasight run --cycle unit
```

You get a panel per stage, a summary table (total, passed, failed, skipped) and the exit code: `0` when everything passed, `1` when a test failed. All exit codes are in [Troubleshooting and Error Codes](CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md).

Each run is recorded in `testo_history.db` (SQLite, next to the config) with its logs under `artifacts/` and a per-run copy of its reports under `.testo/` (set `TESTO_HOME` to put that elsewhere). Add all three to `.gitignore`.

## 4. See what changed

Break a test and run the cycle again, then ask for the difference between the last two runs:

```bash
qasight run --cycle unit       # exit 1
qasight summary --cycle unit
```

```text
Quality score (pass / total)
  Baseline 100.0%  →  50.0%  Δ -50.0 pp
Regressions & risk
  Kind         Test       Before   After
  regression   test_sub   passed   failed
```

Once a cycle has a few runs, `qasight flaky --cycle unit` lists tests that keep flipping between pass and fail.

`qasight report list` shows every recorded run and `qasight diff <BASELINE_ID> <CURRENT_ID>` compares any two. How deltas are classified is in [Delta Comparison Policy](Specs%20&%20ADRs/Delta%20Comparison%20Policy.md).

## 5. Run it in GitHub Actions

```yaml
# .github/workflows/tests.yml
name: tests
on: [push, pull_request]
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install pytest            # your project's test dependencies
      - uses: arieleven/qasight@v1
        with:
          cycle: unit
          python-version: "3.11"         # same Python as above, so it sees your dependencies
          comment-on-pr: "true"          # the results as a pull request comment
```

The action installs `qasight`, runs `qasight run --ci` (one JSON event per line in the log), writes the results to the job summary and fails the job when the cycle fails. With `comment-on-pr: "true"` it also keeps the same summary as one comment on each pull request; a hand-written workflow needs `permissions: pull-requests: write` for that (the one `qasight init` writes has it). Its inputs and outputs, and the GitLab CI template, are in [CI-CD Pipeline Setup](Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md).

## 6. Add other test runners

Add stages to the same cycle; they run in order.

```yaml
    stages:
      - name: unit
        equipment: pytest
        args: [-q, tests]
      - name: flows
        equipment: behave
        args: [features/smoke.feature]     # always name the feature files; needs qasight[bdd]
      - name: web
        equipment: command                 # any runner that writes JUnit XML
        args: [npx, jest, --ci]
        junit_xml: ["reports/junit/*.xml"]
```

`behave` and `behavex` stages need the `bdd` extra: `pip install 'qasight[bdd]'` (in the GitHub Action, `package: "qasight[bdd]"`).

Every key (timeouts, workers, triggers on changed paths, reporters such as Allure) is in [Command Reference](CLI%20Commands/Command%20Reference.md). A runner or reporter that isn't built in can come from a [plugin](CLI%20Commands/Command%20Reference.md#plugins) package.

## Already have CI? Record its results instead

If your tests already run somewhere, skip the YAML and add one step after them. Any runner that writes JUnit XML works (JUnit5, pytest's `--junitxml`, PHPUnit, ...), and so do the files Jest, Vitest, Playwright, `dotnet test`, `go test -json`, Robot Framework, RSpec, k6 and Xcode (`xcresulttool`) write themselves, or an `allure-results` folder. Prefer the runner's own format when it has one: Jest's `--json` report keeps a test file that failed to compile, while `jest-junit` drops it and names every suite `undefined`.

```bash
pip install qasight
qasight ingest 'reports/**/*.xml' --cycle web-unit
```

Before touching CI, `qasight try` shows the pull request comment your latest results would get, without recording anything (`--from-github` uses your last GitHub Actions run's artifacts).

Copy-paste versions for each runner and each CI system (GitHub Actions, GitLab CI, Jenkins, CircleCI) are in [Setup Snippets](Processes%20&%20Guides/Setup%20Snippets.md).

With no arguments, `qasight ingest` finds the result files itself and names the cycle after the folder. In GitHub Actions, the action does the same when the repository has no `qasight.yaml`: put `- uses: arieleven/qasight@v1` with `if: ${{ !cancelled() }}` after your test step and it records the results, keeps the history in the Actions cache and comments on the pull request ([details](Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md#github-action)).

The run gets the same history, `qasight summary`, `qasight flaky` and Compare as a `qasight run`. For one history across every CI job and teammate, run the [Team Server](Processes%20&%20Guides/Team%20Server.md) and add `--server https://your-server --token "$TESTO_API_TOKEN"`. Which file each runner writes: [Command Reference § qasight ingest](CLI%20Commands/Command%20Reference.md#qasight-ingest).

## Using pytest? One flag

```bash
pip install qasight
pytest --qasight                       # or --qasight-cycle api-unit to name the cycle
```

Every run is recorded like `qasight ingest` would record it (the cycle defaults to the project folder's name), with the checkout's branch and commit, so `qasight ui` shows what broke since the last run on your branch. Without the flag the plugin does nothing. Details: [Command Reference § pytest --qasight](CLI%20Commands/Command%20Reference.md#pytest---qasight).

## Upgrading

```bash
pip install -U qasight          # or 'qasight[api]' with the extras you use
```

The run history upgrades itself the first time a newer `qasight` opens it, and keeps every run. `uses: arieleven/qasight@v1` moves to each new 1.x release on its own; pin `@v1.6.0` (any release tag) to hold one. Team server images follow the `1` tag; pull and restart (see [Team Server](Processes%20&%20Guides/Team%20Server.md#how-it-works)). What changed, and anything you need to do, is in the [changelog](https://github.com/arieleven/qasight/blob/main/CHANGELOG.md).

## Next

- **Web UI**: Dashboard, Runs, Compare and AI failure summaries on the same history. `pip install 'qasight[api]'`, then `qasight ui` opens it on <http://127.0.0.1:8000>, and `qasight ui --from-github` opens the history your GitHub Actions runs kept (needs the GitHub CLI). Or look at the [live demo](https://arieleven.github.io/qasight/).
- **Using an AI coding assistant?** [Setup Prompts](Prompts%20&%20Snippets/Setup%20Prompts.md) are copy-paste prompts that do the steps above for you.
- **How it works**: [System Diagram](Architecture/System%20Diagram.md).
