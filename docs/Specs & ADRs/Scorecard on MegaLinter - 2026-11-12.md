---
type: spec
status: current
created: 2026-11-12
updated: 2026-11-12
related: "Release Scorecard - 2026-11-12, Command Reference, CI-CD Pipeline Setup"
---

# Scorecard on MegaLinter

## Context

The first [Release Scorecard](Release%20Scorecard%20-%202026-11-12.md) kept its own list of 20 tools and detected four stacks (python, node, c, GitHub Actions). Run on three outside repositories (CIViC, cBioPortal, cbioportal-frontend), all nine problems found came from the same place: the feature was shaped around a repository like QASight. A Java repository got Bandit on one example script, test data counted as leaked secrets, and a finding cost the same in a 40-line script as in a million-line repository.

Other tools already solve "which linters fit this repository" and "how to turn findings into a grade":

- **MegaLinter** (open source, AGPL, Docker) runs about 100 linters and scanners over 50 languages and formats, picks the ones that fit the files it finds, and writes SARIF plus a JSON summary. It is what Super-Linter and Codacy's analysis do, in one image.
- **SonarQube** rates security by the worst open issue (one critical vulnerability is an E whatever the size) and maintainability by technical debt per line of code. **Codacy** grades by issue density per 1,000 lines and follows a repository's own config files.

Taltal chose (2026-11-12, decision card) to use MegaLinter as the engine and copy the Sonar and Codacy way of scoring, rather than grow our own tool list.

## Decision

- **MegaLinter is the engine.** `qasight/services/scorecard/megalinter.py` runs one `docker run` of `oxsecurity/megalinter` (tag `v9`) on the repository: the flavor image for its main language (60% of source lines; `python`, `javascript`, `java`, `go`, `rust`, `dotnet`, ...), else the full image, or `--megalinter-image` / `QASIGHT_MEGALINTER_IMAGE`. It reads `mega-linter-report.json`, each linter's SARIF and, for linters without SARIF, their error and warning counts. Every linter MegaLinter ran becomes its own tool, `megalinter:<KEY>` (for example `megalinter:PYTHON_RUFF`, shown as "ruff (Python)"), stored, switchable and compared like the built-in tools.
- **The registry's own scanners become a fallback.** Bandit, Semgrep, gitleaks, Flawfinder, pip-audit, npm audit, Trivy and Ruff are marked `in_engine`: when MegaLinter runs they are skipped; when Docker is missing or MegaLinter fails, they run as before. Tools MegaLinter doesn't cover (OpenSSF Scorecard, zizmor, ZAP, Schemathesis, Lighthouse, axe, k6, imports of coverage and mutation reports) stay.
- **What MegaLinter is told**, unless the repository has its own `.mega-linter.yml` (then it is used as is): no spell or link checkers (`DISABLE=SPELL`), no SBOM writers, no TruffleHog (it verifies a found secret by logging in with it; gitleaks and secretlint find the same offline), never fix anything (`APPLY_FIXES=none`), and skip tests, vendored and generated code and docs (`FILTER_REGEX_EXCLUDE`, built from the same path rules as the score). Settings MegaLinter doesn't look for in `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`, `[tool.pylint]`, `[tool.isort]`) are passed as `<KEY>_CONFIG_FILE`.
- **Only source counts.** `paths.py` classes every path as source, test, vendor, generated or docs, for any language. Findings outside source are shown as "not counted". Manifests and lock files always count, since dependency findings point there.
- **Scores follow SonarQube and Codacy** (`rating.py`, mirrored in the UI's `findingsScore`):
  - **Security and supply chain: the worst finding.** None is 100, only low 89 to 80, medium 79 to 70, high 69 to 60, critical 59 to 20; inside a band every doubling of that severity costs 2 points.
  - **Code quality: density.** Weighted findings (critical 10, high 5, medium 2, low 0.5, info 0.1) per 1,000 source lines, with at least 1,000 lines: 2 per KLOC is 90, 5 is 80, 10 is 70, 25 is 60, 100 is 30. A lint error is low and a warning info, like Sonar's code smells; a rule with a `security-severity` keeps it.
  - A category's findings tools are added up and rated together; the overall score is still the mean of the categories, grades still A 90 / B 80 / C 70 / D 60.
- **Duplicate linters on default rules are advisory.** When the project configures a linter for a language (MegaLinter's log shows a rules file from the repository), the other linters MegaLinter runs on that language use its defaults: flake8's line length in a project whose ruff allows 100. Like Codacy, which follows the repository's config, they are shown with "not scored, the project uses ruff (Python)" and left out of the score. Linters MegaLinter runs as non-blocking (formatters, mostly) weigh as info.
- **New findings.** Each finding keeps a fingerprint (severity, rule and file, not the line), so the scorecard shows "N new" per tool against the previous version, and `--no-new` fails the gate on any new high or critical finding: Sonar's "clean as you code".

## Consequences

- Any repository MegaLinter can lint can be scored, and adding a language is MegaLinter's work, not ours. A scan needs Docker and a 2 to 10 GB image; the first pull is slow, so CI jobs cache or pick a flavor. A full scan of QASight takes about 8 minutes.
- QASight doesn't ship MegaLinter (AGPL); it runs the published image, the way a CI job would.
- MegaLinter's network scanners (grype, OSV) need their databases; offline they are reported as failed, not as clean.
- Scores from before this change were made with the old curve and are not comparable with new ones; a version is rescored by scanning it again.
- Real scans on 2026-11-12: fake-api scored 92 (A), 12 of 14 linters ran (grype had no network). QASight's first run scored 63 (D) with code quality 18, all of it noise: 10,772 stylelint errors in Obsidian themes under `docs/`, 2,015 markdownlint errors in the vault, and flake8, pylint, pyright and isort on their defaults next to the project's ruff and mypy. That run is why docs are skipped and duplicate linters are advisory. The second run scored 87 (B): security 82 (10 low checkov findings, 2 low Bandit ones), supply chain 89 (1 low Trivy finding), code quality 89, 27 of 30 tools ran; flake8, pylint, pyright and isort are listed but not scored.
