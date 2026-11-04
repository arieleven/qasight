# QASight (qasight)

Before planning, debugging, or implementing anything in this repo, read [docs/Index.md](docs/Index.md) first — it's the entry point to the Obsidian second-brain vault under `docs/` and links to Architecture, CLI Commands, Roadmap & Strategy, and Specs & ADRs notes (dated plans and phase checklists live in `docs/Archive/`). For AI-agent-specific conventions (which doc to check before touching what), read [docs/Prompts & Snippets/Agent Context Guide.md](docs/Prompts%20&%20Snippets/Agent%20Context%20Guide.md).

## Quick facts

- Package: `qasight`. CLI entrypoint: `qasight` (Typer).
- Config: `qasight.yaml` at repo root defines cycles/stages/reporters.
- Engine flow: `config/loader.py` → `config/resolver.py` → `engine/orchestrator.run_plan()` → `engine/executor.run_stage()`.
- Framework adapters: `qasight/frameworks/` (Pytest, Behave, BehaveX).
- API: `qasight_api/` (FastAPI, `/api/v1/`). Frontend: `frontend/` (Vite + React + Tailwind). CLI and API both run cycles through `services/cycle_run.CycleRunService`.

## Second brain rules

`docs/` is an Obsidian vault and the project's second brain. Its conventions (frontmatter, links, ingest/query/lint/log) are in [docs/CLAUDE.md](docs/CLAUDE.md); `python scripts/docs_vault.py lint` checks them in CI.

1. **Vault first (STRICT).** Before planning, designing, debugging, implementing or answering a question about this project, read [docs/Index.md](docs/Index.md), then the notes for the area you're touching (the [Agent Context Guide](docs/Prompts%20&%20Snippets/Agent%20Context%20Guide.md) says which). Don't rely on this file's summary alone.
2. **Docs stay in sync.** A change to CLI args or exit codes, `qasight.yaml` parsing, the engine flow, API routes, storage, CI workflows or the release process updates the note that owns that fact in the same pull request, and bumps its `updated` date.
3. **Point, don't paste.** If a note already explains something, link to it instead of re-explaining it.
4. **Architectural consistency.** Follow the structure in [ARCHITECTURE.md](ARCHITECTURE.md) and [docs/Architecture/](docs/Architecture/Architecture%20Overview.md): one engine entered through `CycleRunService`, run history behind `qasight/history/`, frontend types generated from OpenAPI. Don't add a second path.
5. **Document significant work (STRICT).** A plan, batch of pull requests, migration or multi-step refactor gets a note in `docs/Specs & ADRs/` (context, decision, consequences) linked from its README, and a line in [docs/log.md](docs/log.md) if the vault's shape changed. If it was big enough to plan, it's big enough to document.
6. **Archive, don't rewrite history.** A note that stops describing the code moves to `docs/Archive/`; archived notes are frozen.

## Rules

- **Behave features must be explicitly targeted** (`behave features/smoke.feature`), never rely on cwd auto-discovery. Applies to CLI wrappers, CI, Dockerfiles, adapters.
- If a change alters CLI args, exit codes, or `qasight.yaml` parsing, update the matching `docs/` note in the same change.
- The vault's own rules (frontmatter, links, ingest per pull request, log) are in [docs/CLAUDE.md](docs/CLAUDE.md). Run `python scripts/docs_vault.py lint` after editing notes.
- Code review runs as a local pre-push hook (`.claude/settings.json`), not in CI.

## Commands

```bash
qasight run --cycle sample-pytests
qasight report --cycle sample-pytests
pytest -q -m "tier_fast and not quarantined" --no-cov
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full setup and pre-PR checklist.
