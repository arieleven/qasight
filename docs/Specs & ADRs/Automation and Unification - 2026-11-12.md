---
type: spec
status: current
created: 2026-11-12
updated: 2026-11-12
related: "New User Journey Automation - 2026-11-01"
---

# Automation and Unification - 2026-11-12

[Index](../Index.md) · On 2026-11-12 Taltal asked for every step a user does by hand that QASight could do itself, every process that lives in several places and could be one, and every flow that could be combined. It builds on the [New User Journey Audit](../Roadmap%20&%20Strategy/New%20User%20Journey%20Audit%20-%202026-11-01.md), which is fully built, and leaves out the plug-and-play work (attach to an existing CI, `qasight run -- <cmd>`, history backfill) planned in another thread.

## Context

The audit found seven items. Taltal chose to build all of them in three waves, one pull request each:

| Id | Item | Wave |
|:-:|---|:-:|
| A1 | One flaky rule everywhere | 1 |
| A3 | Release and environment filled in from CI | 1 |
| A2 | `qasight ci publish`: the GitHub Action's after-run steps (comment, annotations, alerts, flaky issues, upload) as one CLI step for every CI | 2 |
| A5 | Notifications configured in one place in `qasight.yaml` | 2 |
| A4 | Schedules on the team server (digest, manager report email, scorecard rescans) | 3 |
| A6 | A suggested cause for well-known failure messages | 3 |
| A7 | One `action.yml` instead of two copies | 3 |

## Decision

### A1: one flaky rule

Eight services built the per-test observation list that `flaky_service.classify()` reads, each with its own loop. Two of them built it differently:

- The weekly digest (`run_digest.py`) passed bare test cases, without the run's branch, commit or environment. All runs shared one lane, so a pull request that broke a test and fixed it, between green runs on `main`, showed up in the digest as "newly flaky" while the Flaky page said it was not.
- The run's failing tests (`run_failures.py`) added only the commit.

`flaky_service` now has `observations_by_test()`, `flaky_tests()` and `flaky_keys()`, which take `(run, cases)` pairs oldest first. The flaky report, the digest, releases, the team view, delivery metrics and the failure signal all call them, and `run_failures.py` builds its observations with `flaky_service.observation()`. So a test is flaky, or not, the same way on every page. `test_history.py` already used `observation()` and is unchanged.

### A3: release and environment from CI

`run_release.resolve()` and `run_environment.resolve()` gain one last fallback each, used only when nothing else set a value:

- **Release:** the git tag the CI job builds (`ci_provenance.ci_tag()`): GitHub (`GITHUB_REF_TYPE=tag`), GitLab (`CI_COMMIT_TAG`), CircleCI (`CIRCLE_TAG`), Buildkite (`BUILDKITE_TAG`), Jenkins (`TAG_NAME`) and Azure Pipelines (`refs/tags/…`). A tag's run reaches the Releases page with no setup. Branch and pull request runs still belong to no release.
- **Environment:** in CI only, the runner's OS as `os=linux`, `os=windows` or `os=macos` (`ci_provenance.ci_os()`, from `RUNNER_OS`, `AGENT_OS` or the platform). An OS matrix is kept apart with no setup.

`qasight ingest --server` sends the same values with an upload (`ingest_client.client_run_metadata()` now calls the same two `resolve()` functions). `TESTO_AUTO_METADATA=0` turns both fallbacks off.

This reverses one line of [Test Labels, Environments and Releases](Test%20Labels,%20Environments%20and%20Releases%20-%202026-11-02.md), which filled in nothing so it would not split runs nobody asked to split. The OS is the one value that is always right in CI and that a matrix really does split on, and runs outside CI still get nothing.

## Consequences

- The digest, the run page's "flaky" tag and the Flaky page now agree. The digest may list fewer newly flaky tests than before, because its old rule counted a broken-then-fixed pull request.
- After upgrading, a CI history's newest runs carry `os=linux` and older ones carry no environment. They count as two lanes in flaky detection until the window (20 runs) has moved past the upgrade. Set `TESTO_ENV` or `TESTO_AUTO_METADATA=0` to keep one lane.
- Releases built from tags show up on the Releases page without `TESTO_RELEASE`.
- Waves 2 and 3 extend this note as they land.
