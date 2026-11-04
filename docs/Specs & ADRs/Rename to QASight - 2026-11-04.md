---
type: spec
status: current
created: 2026-11-04
updated: 2026-11-04
---

# Rename to QASight - 2026-11-04

[Specs & ADRs](README.md) · [Publishing to PyPI](../Processes%20%26%20Guides/Publishing%20to%20PyPI.md) · [Library Packaging](Library%20Packaging.md)

## Context

The project was called Testosterone, its PyPI package `testo-core` and its command `testo`. Before the 1.6 launch push the name had three problems: "testosterone" reads as a joke in a work setting, "Testo" is crowded (a measuring-instruments company, an OCaml test framework, the `testo` PyPI name is taken), and `testo-core` says nothing about what the tool does. Taltal picked **qasight** (QA + sight, matching the eye logo) on 2026-11-04, after it was checked free on PyPI and GitHub, with qasight.dev, .io and .app unregistered. The same day the GitHub account moved from `taltal-beep` to `arieleven` and the repository from `testosterone` to `qasight`.

## Decision

Rename everything a user types or reads, in 1.6.0, and keep every old name working.

| Area | New | Old name, still working |
|------|-----|-------------------------|
| PyPI package | `qasight` | `testo-core`, released at the same version from [`integrations/testo-core/`](../../integrations/testo-core/README.md): it depends on `qasight==<version>` and holds only the import aliases |
| Command | `qasight` (also `qasight-api`, `qasight-runner-service`) | `testo`, `testo-api`, `testo-runner-service`, declared by both packages |
| Python modules | `qasight`, `qasight_api` | `testo_core`, `testo_api`: a meta path finder in the compat package maps `testo_core.x` to `qasight.x` (the same module object) with a `DeprecationWarning`; `TestosteroneConfig` stays an alias of `QASightConfig` |
| Config file | `qasight.yaml` / `qasight.yml`, `[tool.qasight]` | `testosterone.yaml` / `.yml`, `[tool.testosterone]`, found after the new names (`qasight.paths.CONFIG_FILE_NAMES`) |
| Settings | `TESTO_*` (unchanged) | `QASIGHT_X` fills `TESTO_X` when that isn't set, at `import qasight` |
| pytest | `--qasight`, `--qasight-cycle`, ... | `--testo...`, the same options |
| Plugins | `qasight.frameworks`, `qasight.reporters` | `testo.frameworks`, `testo.reporters`, read too |
| Docker images | `ghcr.io/arieleven/qasight-runner`, `-server`, `-runner-service` | The `testo-*` images stay at 1.5.0 |
| Config schema | `schema/qasight.schema.json` | `schema/testosterone.schema.json`, the same file, both written by `scripts/export_config_schema.py` |
| GitHub, docs site | `github.com/arieleven/qasight`, `arieleven.github.io/qasight/` | GitHub forwards the old repository URLs, `git` remotes and `uses:` lines; the old Pages site does not forward |

Kept on purpose, because changing them would cut existing data or setups loose: files the tool writes (`.testo/`, `testo_history.db`, `testo-summary.md`, `testo-history-*` artifacts), the GitHub Action's cache folder (`~/.cache/testosterone`), the team server's Postgres user and database (`testo`), browser storage keys, the `com.testosterone.project` Docker label, metric and OpenTelemetry attribute names, and the sign-in cookie.

**Why the modules moved too.** Renaming only the PyPI package (a `testo-core` that depends on a `qasight` still shipping `testo_core/`) breaks `pip install -U testo-core`: pip installs the new package, then uninstalls the old one, and that deletes the `testo_core/` files both declare. With the code under `qasight/`, the two packages share no module files. They do share the `testo` scripts, which the uninstall also deletes, so the compat package declares them again and rewrites them last. Both upgrade paths were checked in a clean virtualenv: `pip install -U testo-core` from 1.5.0, and a fresh `pip install qasight` running a `testosterone.yaml` cycle and `pytest --testo`.

The rename itself was a script over every tracked text file except `docs/Archive/`, `docs/log.md` and released `CHANGELOG.md` sections, followed by the hand edits above.

## Consequences

- One release, 1.6.0, carries the new name; no 2.0, since nothing old stops working.
- Each release publishes two PyPI projects from `publish.yml`. Both trusted publishers must name `arieleven/qasight`, workflow `publish.yml`, environment `pypi`; the version bump changes `integrations/testo-core/pyproject.toml` too (a contract test and the workflow fail otherwise).
- `pytest-qasight` replaces `pytest-testo`, which was never uploaded.
- The architecture PNGs in `docs/assets/` were renamed but still show the old name until `scripts/render_architecture_diagram.js` is re-run.
- Removing the old names (the `testo` command, `testosterone.yaml`, the compat package) is a later decision; nothing schedules it.
