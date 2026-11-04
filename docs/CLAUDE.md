---
type: schema
status: current
created: 2026-10-07
updated: 2026-11-04
---

# Vault schema

Rules for the `docs/` vault, the project's second brain. It follows the LLM-wiki pattern (immutable sources, maintained notes, a schema), adapted so every note also reads correctly on GitHub. `python scripts/docs_vault.py lint` checks the mechanical rules and runs in CI.

## Layers

| Layer | Where | Rule |
|-------|-------|------|
| Sources | The code, `qasight.yaml`, `CHANGELOG.md`, merged pull requests | The source of truth. Notes describe it and never override it |
| Notes | Every folder under `docs/` except `Archive/` | Maintained. They describe the code as it is today |
| Archive | `docs/Archive/` | Frozen. Dated plans, audits and checklists; fix a broken link, never the content |
| Schema | This file | Changes when the conventions change |

## Special files

| File | Role |
|------|------|
| [Index](Index.md) | The one entry point. Every note is reachable from it, directly or through its folder's hub note |
| [Log](log.md) | Append-only record of vault operations, newest last |
| [Agent Context Guide](Prompts%20&%20Snippets/Agent%20Context%20Guide.md) | Which note to read before changing what |

## Frontmatter

Every note starts with:

```yaml
---
type: index | schema | log | architecture | reference | guide | spec | roadmap | tracker | archive
status: current | archived
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

- `updated` changes whenever the body changes. CI fails a pull request that edits a note and leaves the date alone.
- `status: archived` is for notes under `Archive/`, and only those.
- Extra keys (`tags`, `related`, `title`) are allowed.

## Writing rules

- **One copy of a fact.** Link to the note that owns it. Per-change history belongs in `CHANGELOG.md`, not in notes.
- **No fact that can rot silently.** A statement is either how the code works now, or carries its date or version ("removed in v1.1").
- **Links** are relative markdown links with spaces encoded as `%20`. No `[[wikilinks]]`; they do not render on GitHub.
- **No orphans.** A new note is linked from [Index](Index.md) or its folder's hub note in the same change.
- **Notes are short.** Facts and structure, with a pointer to the code.

## Operations

### Ingest: every pull request

1. List what the change alters: CLI flags, exit codes, `qasight.yaml` keys, engine flow, API, CI, release steps.
2. Find the notes that own those facts through the [Agent Context Guide](Prompts%20&%20Snippets/Agent%20Context%20Guide.md) and update them in the same pull request. Bump `updated`.
3. A design decision gets a note under `Specs & ADRs/`, linked from its [README](Specs%20&%20ADRs/README.md).
4. A note that stops describing the code moves to `Archive/` with `type: archive` and `status: archived`, and is linked from the [Archive README](Archive/README.md).
5. Run `python scripts/docs_vault.py lint`.

A pull request that changes none of those facts needs no note change.

### Query

Start at [Index](Index.md), follow links, then check the code the note points to. If the answer was missing from the vault, add it to the note that should have had it.

### Lint

`python scripts/docs_vault.py lint` checks frontmatter, broken links, wikilinks and orphans. Contradictions between notes and stale claims need a reader: when you find one, fix it and add a line to the [Log](log.md).

### Log

Add an entry to [log.md](log.md) when the vault itself changes shape: notes added, moved, archived or merged, a lint pass, a schema change. Format: `## YYYY-MM-DD: <operation>` and one or two lines. Routine note updates that ride along with a code change are not logged; the changelog and git history cover them.

## Sync

- **GitHub** is the copy of record. Notes change through pull requests.
- **Local Obsidian vault** is the `docs/` folder of a checkout. It shows whatever that checkout has pulled, so run `git pull --ff-only origin main` to see merged changes.
- **GitHub wiki** is generated from `docs/` by `.github/workflows/wiki-sync.yml` on every push to `main` that touches the vault. Never edit the wiki; the next sync overwrites it.
