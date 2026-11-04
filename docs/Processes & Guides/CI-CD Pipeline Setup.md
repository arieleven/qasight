---
type: guide
status: current
created: 2026-05-02
updated: 2026-11-04
---

# CI Integrations

Just want a snippet to paste? [Setup Snippets](Setup%20Snippets.md) has one per runner for GitHub Actions, GitLab CI, Jenkins and CircleCI, and `qasight init --ci <system>` writes it for you.

Pre-packaged CI wrappers keep orchestration in `qasight`: they install `qasight`, run `qasight run --ci`, and keep its machine output.

## Architecture boundary

- Execution is `qasight run` → `CycleRunService` → `engine.run_plan()`, the same path as a local run and an API execution.
- CI wrappers are thin adapters that only prepare flags and consume the NDJSON stream.
- CI provenance is detected from the environment by `DbBackend` (`qasight/services/ci_provenance.py`) and stored on the run record; repository adapters stay provider-agnostic.

## Output contract in CI

`qasight run --ci` writes one JSON object per line on stdout (`plan_started`, `stage_started`, `stage_finished`, `plan_finished`, plus `cycle_trigger` / `error` when relevant). The last line is `plan_finished` with `exit_code` and per-stage results. Exit codes `0`–`4`: [Troubleshooting and Error Codes](../CLI%20Commands/Troubleshooting%20and%20Error%20Codes.md).

v1.0's "ghost mode" (`uqo run --config … --ghost/--json/--stream-json` and a summary JSON) was removed in v1.1 together with the Docker-based headless runner it drove.

## Canonical CI provenance fields

Run records written in CI include (when the provider exposes them):

- `ci_provider` (`github`, `gitlab`, `buildkite`, `circleci`, `jenkins`, `azure_pipelines`, `generic`)
- `ci_pipeline_id`
- `ci_job_id`
- `ci_commit_sha`
- `ci_ref_name`: the branch. On a GitHub `pull_request` event that is `GITHUB_HEAD_REF` (the PR's branch), not `GITHUB_REF_NAME` (`<number>/merge`)
- `ci_base_ref`: a pull or merge request's target branch (`GITHUB_BASE_REF`, `CI_MERGE_REQUEST_TARGET_BRANCH_NAME`, `BUILDKITE_PULL_REQUEST_BASE_BRANCH`, Jenkins `CHANGE_TARGET`, Azure `SYSTEM_PULLREQUEST_TARGETBRANCH`); unset on branch builds

Runs also record `repository` (`owner/name`, from `GITHUB_REPOSITORY`, `CI_PROJECT_PATH`, `BUILDKITE_REPO` or `CIRCLE_PROJECT_REPONAME`). Runs outside CI record `git_branch` and `git_commit` from the target repository's checkout instead, and `repository` when its `origin` remote is on github.com. `qasight ingest` sends the same fields with the upload.

The PR comment and `qasight report markdown` compare a pull request's run with the latest run of the same cycle on its `ci_base_ref` branch, so a parallel PR's run is never the baseline. With no run on that branch yet they fall back to the previous run on the run's own branch (never another pull request's), a push to the default branch is compared with the previous run on that branch, and they say which one they used ("Since the last run on `main`" or "Since the previous run"). Test names are shown as code spans that nothing in a name can close, so a name can't @mention anyone or render HTML in the comment. When `qasight run` stops before recording a run (bad config), the action publishes no summary, comment, annotations or alert rather than an earlier job's run. This needs history shared between jobs: the GitHub Action keeps it in the Actions cache by default (below), or use a `database.url` or a team server.

## GitHub Action

The action is `action.yml` at the repository root, the copy the GitHub Marketplace lists (`uses: arieleven/qasight@v1`, from 1.4.0); `integrations/github-action/action.yml` is the same action at its older path and keeps working, and `tests/contract/ci/test_action_copies.py` fails if the two differ. Source lives under `integrations/github-action/` (inputs: `results`, `history`, `config-path`, `cycle`, `ci-mode`, `persist`, `python-version`, `package`, `job-summary`, `comment-on-pr`, `annotations`, `slack-webhook`, `teams-webhook`, `notify-when`, `flaky-issues`, `coverage`, `environment`, `release`, `history-artifact`, `ai-summary`, `comment-artifact`, `post-comments-from`, `server-url`, `server-token`, `github-token`; outputs: `exit_code`, `status`, `summary_json`, `summary_path`, `comment_dir`).

```yaml
permissions:
  contents: read
  pull-requests: write        # only for comment-on-pr
steps:
  - uses: arieleven/qasight@v1
    with:
      cycle: smoke
      comment-on-pr: "true"
```

**No config file.** With no `qasight.yaml` (or `[tool.qasight]`) in the workspace, or when `results` names files, the action runs `qasight ingest --ci` instead of `qasight run`: it records the result files an earlier step wrote (with no `results`, every result file in the workspace that git doesn't track, found by content), under `cycle` or a cycle named after the repository folder, and `comment-on-pr` (default `auto`) is on. A configured `qasight run` keeps the comment off unless asked for, as before. Add `if: ${{ !cancelled() }}` so a red test step is still recorded.

```yaml
steps:
  - uses: actions/checkout@v4
  - run: go test -json ./... > test-results/go.json
  - uses: arieleven/qasight@v1
    if: ${{ !cancelled() }}
```

**History between runs.** `history: auto` (default) points `DATABASE_URL` at `~/.cache/qasight/testo_history.db` and restores that folder from the Actions cache before the run and saves it after (`actions/cache/restore` / `save`, a new key per run restored by prefix `testo-history-<workflow>-<job>-`), so the summary and comment compare with earlier runs and a pull request with its target branch (a pull request can read its base branch's caches). Only the database is cached, not per-run report copies. It is skipped when `DATABASE_URL` or the config's `database.url` is set; `history: off` turns it off. When the restore finds nothing (the job's first run, or GitHub deleted a cache unused for 7 days), the job summary says so, instead of a first run passing silently.

**Browse CI's history locally.** The cached history is also uploaded as a `testo-history-*` artifact (kept 7 days; `history-artifact: false` turns it off), and `qasight ui --from-github` downloads the newest one with the GitHub CLI and opens the UI on it, see [`qasight ui`](../CLI%20Commands/Command%20Reference.md#qasight-ui).

**Pull requests from forks.** Their token is read-only, so the comment can't be posted from their run. `comment-artifact: "true"` uploads it as a `testo-comment` artifact instead, and a `workflow_run` workflow posts it with `post-comments-from` (no fork code is checked out; only marker-prefixed, size-capped files are posted, on the pull request whose head is the tested commit). `qasight init --forks` writes both; the YAML is in the [Action README](https://github.com/arieleven/qasight/blob/main/integrations/github-action/README.md#pull-requests-from-forks).

**AI summary.** `ai-summary: "true"` adds an AI summary of the failures to a failed run's summary and comment. The provider comes from the step's `env` (`TESTO_AI_PROVIDER` plus a key secret, see [`qasight ai`](../CLI%20Commands/Command%20Reference.md#qasight-ai)); a provider that doesn't answer adds one line and never fails the job.

**Team server.** `server-url: https://testo.example.com` with `server-token: ${{ secrets.TESTO_API_TOKEN }}` also uploads each recorded run there with `qasight ingest --server`: the result files for a recording, or each stage's `allure-results` for a cycle the action ran (one stage keeps its name; several are uploaded as one `junit` stage). The summary and comment still come from the cached history. `qasight init --server URL` writes both inputs and, with `gh` and `$TESTO_API_TOKEN` set, stores the secret.

After a persisted run the action renders it with `qasight report markdown` and writes it to the job summary (`job-summary`, default on). With `comment-on-pr: "true"` on `pull_request` events it keeps one comment per cycle on the pull request, found by its hidden marker and edited on later pushes. Both are best-effort: an error, such as a fork's read-only token, is a workflow warning and never changes the exit code. `package` is what gets pip-installed (`qasight` by default; `qasight[bdd]` for cycles with `behave` or `behavex` stages; this repository's own CI passes `.`; several requirements are separated by spaces, `qasight pytest-xdist`). With `cycle: all` every cycle gets its own job summary section, PR comment, annotations and alert. Inputs reach the action's shell steps as environment variables, never as script text, so a branch name in an input can't run commands. The GitLab template's `TESTO_PACKAGE` variable does the same. Design: [GitHub Action PR Comment - 2026-10-19](../Specs%20&%20ADRs/GitHub%20Action%20PR%20Comment%20-%202026-10-19.md).

The action also prints failing tests as annotations on the diff (`annotations`, default on, no permission needed; [`qasight report annotations`](../CLI%20Commands/Command%20Reference.md#subcommands)). Pass `slack-webhook: ${{ secrets.SLACK_WEBHOOK_URL }}` and/or `teams-webhook` to get a chat alert ([`qasight notify`](../CLI%20Commands/Command%20Reference.md#qasight-notify), `notify-when` default `failure`). `flaky-issues: "true"` opens one issue (label `flaky-test`) per test the run made flaky and needs `issues: write`. All three are best-effort like the summary. `coverage: coverage.xml` (space-separated; Cobertura, JaCoCo, LCOV or Go cover profiles your tests write) stores line coverage with the run, and the summary and PR comment show it with its change against the baseline ([`qasight run --coverage`](../CLI%20Commands/Command%20Reference.md#options)). In a matrix, `environment: browser=${{ matrix.browser }}, os=${{ matrix.os }}` records what each job tested on, so a test that fails on one browser only reads as failing there, not as flaky ([`--env`](../CLI%20Commands/Command%20Reference.md#options)). On a tag build, `release: ${{ github.ref_name }}` records which release the run tested, for the Releases page ([`--release`](../CLI%20Commands/Command%20Reference.md#options)). Design: [Alerts Where Developers Look - 2026-10-22](../Specs%20&%20ADRs/Alerts%20Where%20Developers%20Look%20-%202026-10-22.md).

Issues that also close themselves come from `qasight` itself: set `TESTO_ISSUES: github` in the step's `env:`; the action already passes its `github-token` input as `TESTO_GITHUB_TOKEN` (outside the action, set `GITHUB_TOKEN: ${{ github.token }}`, or run [`qasight issues sync`](../CLI%20Commands/Command%20Reference.md#qasight-issues-sync) as a later step). Every recorded run then opens one issue per test it made flaky, found again by a hidden marker so there is never a duplicate, and closes it once the test has passed its last `TESTO_ISSUES_CLOSE_AFTER` runs. The job needs `permissions: issues: write`; without it GitHub answers 403 and the run prints one `qasight issues:` warning, nothing more. It skips tests whose open issue the action's `flaky-issues` already made, so the two can be on together. Design: [Flaky Test Issues - 2026-11-01](../Specs%20&%20ADRs/Flaky%20Test%20Issues%20-%202026-11-01.md).

```yaml
permissions:
  contents: read
  issues: write               # TESTO_ISSUES=github or flaky-issues
steps:
  - uses: arieleven/qasight@v1
    env:
      TESTO_ISSUES: github
      TESTO_ISSUES_ASSIGN: "1"
    with:
      cycle: nightly
```

## GitLab include template

Template lives at `ci/gitlab/testo.gitlab-ci.yml`.

```yaml
include:
  - project: "arieleven/qasight"
    file: "/ci/gitlab/testo.gitlab-ci.yml"

variables:
  TESTO_CYCLE: "smoke"
```

Variables: `TESTO_CONFIG_PATH` (empty = discovery), `TESTO_CYCLE` (empty = the only cycle), `TESTO_PERSIST` (`true` default). Artifacts: `testo-output.ndjson` and `testo-summary.json` (the `plan_finished` line).

## Runner image

`Dockerfile.qasight-runner` builds an image whose entrypoint is `qasight`. Use it as the CI job image when you want a pinned toolchain; stages run as subprocesses inside that job container. See [Publishing Docker Images](Publishing%20Docker%20Images.md).

## Tiered test harness commands

Tier selection is marker-driven and shared between local runs and GitHub Actions:

- Fast required gate:
  - `python -m pytest -q -n auto -m "tier_fast and not quarantined" --maxfail=1 --no-cov` (CI runs it on every core with `pytest-xdist`, which the `dev` extra installs)
- Heavy optional gate:
  - `python -m pytest -q -m "tier_heavy and not tier_external" --maxfail=1 --durations=25`
- External nightly/release gate:
  - `python -m pytest -q -m "tier_external and cleanup_required" --maxfail=1 --durations=50`

Reference CI definitions:

- GitHub: `.github/workflows/ci.yml` (unified pipeline: format (ruff lint + format check, mypy, and `lint-imports`, the import-linter layer contracts from `pyproject.toml` `[tool.importlinter]`, all blocking) and test run in parallel, then deploy on a release; the fast-required gate lives in its `test` job on Python 3.11, followed by the heavy tier, and `test_python_versions` runs the same suite on 3.12, 3.13 and 3.14 without the coverage gate), `.github/workflows/pr-heavy.yml`, `.github/workflows/nightly-external.yml`, `.github/workflows/release-gate.yml`. Code review runs via a local pre-push hook (`.claude/settings.json`), not in CI.

All tier jobs upload diagnostics artifacts (`logs`, summary JSON, API responses, screenshots when present) on failure, and external suites run with `external-e2e` concurrency isolation.

## This repository's workflows

What runs on QASight itself, as opposed to the wrappers above that run it in other projects. Every workflow is under `.github/workflows/`.

| Workflow | Runs on | What it does |
|----------|---------|--------------|
| `ci.yml` | pull request, push to `main`, release | Blocking gates: ruff lint and format, mypy on `qasight` and `qasight_api`; the fast pytest suite with the coverage gate; frontend typecheck, ESLint, generated API types up to date, vitest and build; `CHANGELOG.md` touched; docs vault lint; the docs site with `mkdocs build --strict`; `examples/getting-started/` through the local GitHub Action, once with the PyPI release and once with this checkout (which also posts the PR comment); builds the sdist and wheel, runs `twine check --strict`, installs the wheel with no extras and runs a cycle (`scripts/package_smoke.sh`). Builds the wheel on a release |
| `commitlint.yml` | pull request | Conventional commit messages |
| `changelog-on-main.yml` | push to `main` | Drafts a `CHANGELOG.md` [Unreleased] entry from the pushed commits with Claude and commits it to `main`; skips when its bot secrets are missing |
| `pages-demo.yml` | push to `main`, nightly | Runs the self-test and fake-api cycles and publishes the read-only UI and the docs site to GitHub Pages ([GitLab Pages Demo](GitLab%20Pages%20Demo.md)) |
| `wiki-sync.yml` | push to `main` touching `docs/` | Regenerates the GitHub wiki from `docs/` ([Docs Vault and Wiki Sync](../Specs%20&%20ADRs/Docs%20Vault%20and%20Wiki%20Sync%20-%202026-10-07.md)) |
| `mirror-to-gitlab.yml` | push to `main` | Mirrors the repo to GitLab when `GITLAB_MIRROR_URL` / `GITLAB_MIRROR_TOKEN` are set |
| `pr-heavy.yml` | labeled pull request, manual | Heavy test tier |
| `nightly-external.yml` | manual (nightly schedule paused; uncomment its `schedule:` lines to re-enable) | External test tier |
| `release-gate.yml` | manual | Release checks before publishing |
| `dependabot.yml` (config, not a workflow) | weekly | Grouped dependency update PRs for pip, npm (`/` and `frontend/`) and GitHub Actions, committed as `chore(deps)` with the `no-changelog` label so commitlint and `changelog_required` pass |
| `publish.yml`, `docker-publish.yml`, `artifactory-publish.yml` | GitHub Release published (`publish.yml` and `docker-publish.yml` can also be run by hand for an existing tag) | PyPI, GHCR runner image, JFrog Artifactory ([Publishing to PyPI](Publishing%20to%20PyPI.md), [Publishing Docker Images](Publishing%20Docker%20Images.md)) |
| `move-major-tag.yml` | GitHub Release published (not prereleases), manual | Force-moves the `v<major>` tag (e.g. `v1`) to the release's commit |

## Versioning policy

- GitHub action: publish immutable semver tags (`v1.0.0`, `v1.0.1`, ...), keep `v1` moving to latest stable `v1.x` (done by `move-major-tag.yml`), and recommend SHA pinning for strict supply-chain policies.
- GitLab template: pin `include` to immutable tag or commit SHA in production pipelines.
- Runner image tags:
  - immutable: `v1.x.y`, `sha-<commit>`
  - moving: `v1`, `latest`
- Compatibility rule: `qasight-runner:v1.x.y` must embed a `qasight` `1.x.y` compatible CLI contract (`qasight run --ci` NDJSON and exit semantics).

## Official documentation

| Topic | Reference |
|-------|-----------|
| Docker Engine | https://docs.docker.com/engine/ |
| Docker Compose | https://docs.docker.com/compose/ |
| Compose file spec | https://docs.docker.com/compose/compose-file/ |

---
**Context & Links:**
- [QA Strategies § CI and streaming output](../Testing%20Workflows/QA%20Strategies.md#ci-and-streaming-output), [Command Reference](../CLI%20Commands/Command%20Reference.md), [Architecture Overview](../Architecture/Architecture%20Overview.md), [Deep Dive - Execution Logic](../Architecture/Deep%20Dive%20-%20Execution%20Logic.md)
- Gates (v1.0, historical): [Release Checklist - Phase 2 CI Integrations](../Archive/Release%20Checklist%20-%20Phase%202%20CI%20Integrations.md), [Release Checklist - Phase 2 Ghost Mode](../Archive/Release%20Checklist%20-%20Phase%202%20Ghost%20Mode.md)
