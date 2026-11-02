---
type: spec
status: current
created: 2026-11-02
updated: 2026-11-02
---

# Lifecycle Fixes - 2026-11-02

[Specs & ADRs](README.md) · [Team Server](../Processes%20&%20Guides/Team%20Server.md) · [Command Reference § testo clean --history](../CLI%20Commands/Command%20Reference.md#testo-clean---history)

## Context

A lifecycle and "year two" review (2026-11-02) checked everything Testosterone stores or starts for create, view, edit, delete, limit, stop, backup and upgrade, then imagined a team server after 12 months: 100k runs, a full disk, a crash mid-run, a leaving employee, a rotated secret. Delete, stop and run settings came separately in pull request #292 (Run Deletion and Stop). The review ranked 15 more gaps; Taltal chose the top four groups (A to D) for 1.6.0:

- **A. Storage limits.** Report archives (a zip of each run's cycle folder, stored in the database) were never deleted. The Docker team server had no retention, because retention was read only from `testosterone.yaml`, which the server doesn't have. Each run's files were copied in full, videos included.
- **B. Storage visibility.** Nothing showed how big the history was or how full the disk was, and backups sat on the same volume as the data.
- **C. Runs that die mid-way.** Runs are written to the history when they finish, so a run killed with `kill -9`, out of memory, or on a server that crashed left no trace at all.
- **D. Token rotation.** `TESTO_API_TOKEN` was the CI upload key, an admin login and the key that signs sign-in cookies. Rotating it signed everyone out and broke every CI job at the same moment.

## Decision

**A.** `prune_report_archives()` applies `history.retention` to report archives with the same rules as runs; `testo clean --history` and the server's timer run both. `testo report delete --id` deletes archives by hand. `TESTO_RETENTION_DAYS` and `TESTO_RETENTION_RUNS_PER_CYCLE` override the file (`0` switches a limit off), so a server without a config is limited by its environment (`with_env_overrides()` in `config/history_section.py`, `effective_history()` in `history/retention.py`). `history.max_file_mb` / `TESTO_HISTORY_MAX_FILE_MB` leaves bigger files out of a run's history copy and its archive, and the run lists them in `snapshot_skipped_files`. A copy that fails (full disk) is logged as a warning, the half copy is removed, and the run notes `snapshot_error`. `testo server init` writes `TESTO_RETENTION_DAYS=180` and `TESTO_HISTORY_MAX_FILE_MB=100` for new servers. Existing servers keep everything until someone sets a limit, so an upgrade never deletes history by itself.

**B.** `history/storage.py` measures runs, report archives and their size, the `static/history/` files, the database (SQLite file, `pg_database_size`, MySQL `information_schema`) and free disk. `GET /api/v1/storage` serves it, cached 5 minutes; the Runs page shows one storage line and a warning when free space is under `TESTO_LOW_DISK_PCT` (default 10%). `/api/v1/health/ready` gains a `disk` check: `warning` keeps it 200, `degraded` (under 200 MB) makes it 503. `testo doctor` prints `Disk` and `History size` rows. The compose file keeps backups in their own `testo_backups` volume and removes `.partial` files left by a backup cut short.

**C.** The cycle lock file (`<artifacts>/<cycle>/.lock`, from [Engine Fixes](Engine%20Fixes%20-%202026-11-01.md)) becomes the run's journal: while a run holds the lock it holds a marker (cycle, pid, host, start time), emptied when the run ends in Python, however it ends. A marker found when the lock is next taken belongs to a run whose process died. The next run of that cycle records it (`record_unfinished_run()`), and so does the API at startup for every cycle under its artifacts root (`recover_unfinished_runs()`), as a failed run with `error: interrupted`, exit 3 and no test results. Lock files from before 1.6 hold a bare pid and are ignored. The OS file lock means a run still going on the same machine is never mistaken for a dead one; a run on another machine has its own artifacts folder.

**D.** `TESTO_API_TOKEN_PREVIOUS` is accepted next to the token (bearer, token sign-in and token cookies) during a rotation. `TESTO_SESSION_KEY`, when set, signs account cookies and report tickets instead of the token, so a new token keeps people signed in; without it, the previous token also verifies them while it is set. `testo server init` generates a session key. Team Server has a "Rotating the token" section.

## Consequences

- A team server made by `testo server init` now has bounded history, a storage line and a disk check out of the box. Older ones gain the switches and keep today's behaviour until they set them.
- Report archives finally shrink with everything else. They are still a second copy of a run's files; dropping them in favour of the history copy is a later step.
- A crashed run shows up as a failed run with a reason instead of vanishing. Its stages and tests are unknown, so it counts as a failed build in pass-rate and red-time numbers, which is what happened.
- Rotating the token is a three-step routine instead of an outage. Named, revocable CI tokens (one per pipeline) remain a later step.
- Not done here (still in the review's list): automatic backup before a schema upgrade (E), faster per-test history queries (F), audit log and resource history retention, cycle rename and delete, ingest retries (G), and a reusable lifecycle checklist note (H).
