---
type: spec
status: current
created: 2026-10-08
updated: 2026-11-04
---

# Docs Vault and Wiki Sync - 2026-10-07

## Context

The project's notes existed in three places that drifted apart: `docs/` in the repo, an Obsidian vault on the owner's machine (a shortcut to `docs/` of a local clone that nobody pulled), and a GitHub wiki that was copied by hand once and had not changed since 2026-06-24. Notes used `[[wikilinks]]` that did not render on GitHub, had no metadata, and nothing checked them. AI agents working on the repo had no rule telling them to read or update the notes.

## Decision

Introduced in [#86](https://github.com/arieleven/qasight/pull/86), on top of the vault restructure in [#74](https://github.com/arieleven/qasight/pull/74):

| Change | Where |
|--------|-------|
| `docs/` is the one source; the vault and the wiki are views of it | [Vault schema § Sync](../CLAUDE.md#sync) |
| A schema for the vault: layers, frontmatter, writing rules, ingest / query / lint / log | [Vault schema](../CLAUDE.md) |
| Frontmatter (`type`, `status`, `created`, `updated`) on every note; append-only [log](../log.md) | every note |
| `scripts/docs_vault.py lint` checks frontmatter, broken links, wikilinks, orphans and stale `updated` dates; runs in CI and as a contract test | `scripts/docs_vault.py`, `tests/contract/qasight/test_docs_vault.py` |
| `.github/workflows/wiki-sync.yml` regenerates the wiki from `docs/` on every push to `main` that touches the vault | [CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md) |
| Six "second brain" rules in the root `CLAUDE.md`: vault first, docs in sync, point don't paste, architectural consistency, document significant work, archive don't rewrite | `CLAUDE.md` |

The wiki export flattens note paths into page names (`Specs & ADRs/README.md` becomes `Specs-and-ADRs`), rewrites note links to wiki pages, and rewrites links to anything else in the repo, images included, to GitHub URLs on `main`. Mermaid blocks pass through unchanged and render in all three views.

## Consequences

- Never edit the wiki: the next sync overwrites it.
- The local Obsidian vault shows only what its clone has pulled. Run `git pull --ff-only origin main` in that clone, or let the Obsidian Git plugin pull on a timer (pull only, no commit or push).
- Images must be committed under `docs/assets/` and embedded from a note to appear in the vault and the wiki. Until 2026-10-08 the architecture diagram was embedded only in the root `README.md`, so it showed on GitHub but not in Obsidian or the wiki; [System Diagram](../Architecture/System%20Diagram.md) fixed that.
- A pull request that edits a note without bumping its `updated` date fails CI, unless the note is already dated that day.
