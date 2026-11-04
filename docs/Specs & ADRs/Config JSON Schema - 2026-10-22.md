---
type: spec
status: current
created: 2026-10-22
updated: 2026-11-04
related: "Command Reference, Getting Started"
---

# Config JSON Schema - 2026-10-22

## Context

`qasight.yaml` had no JSON Schema, so editors could not suggest its keys or flag a typo such as `worker:` until `qasight config validate` ran. Most YAML-driven tools ship one; editors with a YAML language server (VS Code's Red Hat YAML extension, JetBrains, Neovim) load it from a URL or a `# yaml-language-server: $schema=...` line.

## Decision

1. `qasight/config/json_schema.py` builds a draft-07 schema from the constants in `qasight/config/schema.py` (`SUPPORTED_FRAMEWORKS`, `SUPPORTED_TIERS`, `SUPPORTED_REPORTER_TYPES`, `Defaults`). The loader stays the source of truth for what loads; the schema mirrors the keys `qasight/config/loader.py` reads.
2. `scripts/export_config_schema.py` writes it to `schema/qasight.schema.json`. CI runs it with `--check`, the same way `export_openapi.py` guards `frontend/openapi.json`. Editors load it from `https://raw.githubusercontent.com/arieleven/qasight/main/schema/qasight.schema.json`.
3. `qasight config init` and `qasight init` write the `$schema` line at the top of the file, and the repo's own configs point at the file by relative path.
4. `equipment`, `framework` and reporter `type` suggest the built-in names but accept any string, because [plugins](Plugin%20API%20-%202026-10-19.md) add their own. Unknown keys on the top level, `defaults`, cycles and stages are errors, which is what catches typos. Reporter entries allow any key, since those are reporter options.
5. Only the canonical `cycles:` layout is described. Legacy `plans:` and `runs:` still load, but the schema flags them.

## Options considered

- **Generate from the dataclasses.** Rejected: the dataclasses are the loaded form, not the YAML. YAML has `equipment`, `if` and `args` as a string or list; the dataclasses have `framework`, `if_expr` and a tuple.
- **Switch the loader to Pydantic and use its schema.** Rejected for now: a rewrite of the loader for a small editor feature.
- **Submit to SchemaStore.** Tried and declined for now: [SchemaStore/schemastore#6546](https://github.com/SchemaStore/schemastore/pull/6546) (2026-10-24) was closed by a maintainer, who said space in the catalog is limited and to open a new PR once the tool is widely used. A duplicate opened by mistake on 2026-10-26 ([#6552](https://github.com/SchemaStore/schemastore/pull/6552)) was closed the same day. Until then, editors need the `$schema` line, which `qasight config init` and `qasight init` already write.

## Consequences

- A new loader key needs a matching schema entry. `tests/contract/qasight/test_config_json_schema.py` validates the repo's configs and the starter template against the schema, checks a set of invalid configs are flagged, and fails if the committed file is stale.
- The schema URL follows `main`, so a key added on `main` shows up in editors before it is on PyPI.
