---
type: spec
status: current
created: 2026-10-25
updated: 2026-11-04
related: "Product Review, Product Roadmap Work"
---

# Product to Ten - 2026-10-25

## Context

The second pass of the [product review](../product-review.md) scored the product 7.5 and listed what a 10 needs in each area except trust, which only users and a launch move. Taltal asked for every other area to go as far toward 10 as code can take it, in waves of separate pull requests, merged when green. Earlier roadmap work is in [Product Roadmap Work - 2026-10-25](Product%20Roadmap%20Work%20-%202026-10-25.md).

## Decisions

### Recorded cycles get a page (P10)

`GET /api/v1/cycles` and `GET /api/v1/cycles/{cycle}` read `qasight.yaml`, so a cycle created by `qasight ingest`, the path the README leads with, was missing from Cycles and its page answered 404. Both routes now add the cycles that exist only in run history (`history.read_model.latest_run_per_cycle`, the newest run of each among the last 500), after the config's own, with `source: "history"`. Their detail page takes its stages from the latest run, and the UI hides the Run panel and the config-only stage fields (args, timeout, workers). A cycle that is in both stays a config cycle.

With no config file at all, as with `qasight ui` over ingested runs, the read routes list the recorded cycles instead of answering 503. A `config_path` the caller names must still exist, and an invalid config is still a 503.

### "New" means new against the baseline, and the run says what it tested (P11, P12)

The run page's failing-tests card counted a test as new when the cycle's previous run, on any branch, passed it. On a repo with parallel pull requests that run is often someone else's branch. `load_run_failures` now uses the PR comment's rule ([Product Roadmap Work](Product%20Roadmap%20Work%20-%202026-10-25.md), P3): a run with a recorded target branch is judged against the latest run on that branch, and any other run against the run before on its own branch. A test is `new` when it isn't failing on that baseline. Streaks ("Failing for N runs") and flakiness follow the run's own branch; runs that recorded no branch stay in, since they can't be told apart. `GET /api/v1/runs/{run_id}/failures` adds `new` per test and `baseline_run_id`, plus `baseline_branch` when the baseline is another branch, so the card can say "1 new since main, 3 already failing on main" and mark a test "Also failing on main".

`GET /api/v1/runs/{run_id}` now returns the `branch`, `commit`, `base_branch` and `repository` the run recorded, and the run page shows them under its title, with the commit linked on GitHub.

### Compare opens on something, and a run says how to rerun its failures (P13, P14)

A bare `/compare` opened with two empty pickers. It now shows the latest run against the run before it in the same cycle, until the user picks, and each run option names its branch (`GET /api/v1/runs` adds `branch`), so two runs from the same minute can be told apart.

`GET /api/v1/runs/{run_id}/failures` adds `rerun_commands`: one command per runner that reruns just the failing tests, built by `services/rerun_commands.py` from each test's stage, that stage's recorded framework and the test's names. pytest gets node ids (`pytest tests/api/test_orders.py::test_cancel`), Playwright `npx playwright test --grep`, and Behave `--name '^scenario$'`. JUnit or command stages count as pytest only when the names point at a Python test module; anything else is left out rather than guessed, and over 30 tests gives no command (rerun the stage). The run page shows the commands above the failing tests with a Copy button. More runners were added on 2026-10-26: [Runner Coverage](Runner%20Coverage%20-%202026-10-26.md).

### Accessibility and copy polish (A8, A9, C8, C10)

- The skip link sits in a `nav` labelled "Skip links", so every piece of content is inside a landmark (axe "region").
- Targets reach 24 px (WCAG 2.2, 2.5.8): the demo notice's close button is 44 px, the "Read-only demo" badge is taller, and run links and "compare" links in lists get a padded hit area that doesn't change the row's layout.
- Copy: "Generate AI summary"; dashboard trends read "Better than last run", "Worse than last run" or "No change"; the test pyramid says "Healthy pyramid", "Top-heavy", "Integration-heavy" or "Mixed tiers" with a plain sentence under it (the API's `message` changed with it); health percentages are whole numbers (`formatPercent`), keeping one decimal only when rounding would show 100% for a run with a failure or 0% for one with a pass.
- The OpenAI default model is `gpt-4.1-mini` instead of `gpt-4o-mini`. A reasoning model (the `gpt-5` family) would need `max_completion_tokens` and no `temperature`, which the OpenAI provider doesn't send, so the default stays a non-reasoning model.
- `qasight config init --help` and `qasight config-db --help` no longer show an RST-style double-backticked path, or lose `[tool.qasight]` to Rich markup.

### Text alternatives for charts, and a 200 % zoom pass

The per-test duration chart's label now reads its values ("Duration per run, oldest first: 100 ms passed, …"), the flaky card's outcome strip is an image with a label (a label on a plain `span` is not announced), and the pyramid's bars are hidden from screen readers since the count beside each is text. At 200 % zoom (a 640 × 450 CSS viewport for 1280 × 900) the dashboard, cycles, a cycle, runs, a run, Compare, the try-it page and AI settings have no horizontal scroll.

### The dashboard answers "what needs me?" first (D7), and the first-run page fits `pip` users

The dashboard opened on four KPI tiles for the latest run, whatever its cycle, then rollup chips ("0 unknown") and a "Quick links" card. It now leads with a "Needs attention" card from `GET /api/v1/dashboard/attention` (`services/attention.py`): for the latest run of each of the ten most recently run cycles, which are red, with their failing and new-failure counts and how many runs in a row they have been red (on the run's branch); tests the latest run made flaky (`flaky_service.newly_flaky_tests`); and the tests that got slower most (`test_history`'s "Got slower"). Each line links to the run or test page that explains it, and a quiet day reads "All 3 cycles passed their latest run. Nothing needs you." The KPI tiles stay, below it, without their decorative icons; the rollup and quick-links cards are gone (report links moved to the Recent runs card's header). The overview endpoint is unchanged.

The no-runs page told people to press Run on a cycle, which a `pip install` user with no `qasight.yaml` can't. It now offers the three ways in: `qasight ingest` for existing JUnit output, `qasight config init` and `qasight run`, or the try-it page.

### `pytest --qasight` records local runs (time to first value)

The fastest way to a first run was `pytest --junitxml` plus `qasight ingest`, two commands to remember. qasight now ships a pytest plugin (`qasight/pytest_plugin.py`, entry point `pytest11`) that does both: `--qasight` (or `--qasight-cycle NAME`, or `TESTO_RECORD=1`) points pytest's own `--junitxml` at a temporary file inside the rootdir and, after the session, records it with `execute_ingest_command`, the function behind `qasight ingest`. So there is still one ingest path; the plugin only saves the typing. The report sits inside the rootdir so the run records the checkout's branch and commit, which "New since main" and the rerun command use.

The plugin is registered for every pytest run on a machine with qasight installed, so it is inert without the flag, imports nothing from qasight until it records, skips xdist workers, and turns any recording failure into one summary line instead of a different exit code.

### Screenshots, videos and traces on the test page (feature depth)

Playwright JSON imports already copied each attempt's attachments next to the test's Allure result, but only the Allure report showed them. `GET /api/v1/analytics/test-history` now adds `last_failure_attachments` (`services/test_attachments.py`): the files the last failing run kept for the test, the last attempt's first, each with its kind (`image`, `video`, `trace`, `text`, `file`) and a URL under `/history`, like report links. The test page shows screenshots and videos in its "Last failure" card and offers the trace as a download with the `npx playwright show-trace` command. Only files inside the run's own `static/history/<run_id>/` snapshot are listed, and `/history` already serves attachments with a `sandbox` Content-Security-Policy.

### Flakiness over time per test (feature depth)

A test was flaky or not, over the whole window. Each point of `GET /api/v1/analytics/test-history` now carries `flip_rate`: the share of outcome changes (pass to fail or back, skips ignored) over the five runs ending there (`test_history.FLIP_WINDOW`), the same rule `flaky_service` uses for the whole window. The test page draws it as a "Flakiness over time" chart once the test has flipped at all, with "Outcome changed in 60% of the last 5 runs, up from 20% five runs before", so a fix that worked, or a test that is getting worse, shows.

### Triage memory: mark a failure's cause once (feature depth)

Every red run asked the same question again: is this ours, the test's, or the CI box's? Someone can now answer it once. On a failed run's page each failing test has "Mark the cause" (bug in the product, bug in the test, environment or infrastructure, flaky, other, and a free-text note such as an issue link). The answer is stored per cycle and failure message in a new `failure_triage` table (`services/triage.py`, `TriageStore`), keyed on `run_failures.message_signature`, the rule that already groups a run's failures under "Same cause". So every later failure in that cycle whose message differs only in numbers, hex ids or quoted values shows the cause, says it came "from an earlier failure with the same message in run …", and can be changed or cleared there. `GET /api/v1/runs/{run_id}/failures` adds `triage` per test; `PUT /api/v1/triage` marks and `DELETE /api/v1/triage?cycle=&signature=` clears, as writes guarded like starting a run (API token or a `runner` account, whose name is recorded); `GET /api/v1/triage` lists them. A cause belongs to one cycle, so a message shared by two unrelated suites isn't labelled for both. The static demo shows causes read-only.

### Third pass fixes: a cycle's run history, a chart scale, copy (P15, D8, C11 to C13)

The third review pass found one Medium: you couldn't see one cycle's runs over time. Runs mixed every cycle, and a cycle's page had its tests but not its runs. `GET /api/v1/runs` takes `cycle=` (`list_run_sessions(cycle=)` looks back 500 runs so a quiet cycle still has a history), Runs has a cycle picker kept in the URL (`/runs?cycle=web`) with "Passed 2 of the last 6 runs (33%)", and the cycle page opens with a "Recent runs" card: that line, one linked square per run oldest first, the latest three runs and "All runs of web". The static build answers `/runs` with every run, so the client filters by cycle as well. The duration chart on the test page gets a scale (its longest value and zero), so a steady test no longer looks busy. Slowdown percentages use `formatPercent` everywhere, the dashboard says "All 3 cycles are red." when none passed, and Runs says "Every recorded run".

## Consequences

- A team that only ingests JUnit gets the same cycle page as one that runs cycles through `qasight run`, apart from starting runs.
- The attention card reads per-test history for up to ten cycles on each dashboard load. At this scale that's fine; caching it per latest run id is the next step if it gets slow.
- A rerun command is a best guess from recorded names: it can miss a parametrized pytest id or a test whose module path the runner didn't record. It is shown only when every test in the group maps to a name.
- `GET /api/v1/cycles` reads up to 500 recent runs on each call. Fine at this scale; a distinct-cycles query in the repository is the next step if it shows up.
