---
type: guide
status: current
created: 2026-10-22
updated: 2026-11-05
---

# Team Server

[Index](../Index.md) · [Command Reference § qasight ingest](../CLI%20Commands/Command%20Reference.md#qasight-ingest) · [Ingest and Team Server - 2026-10-22](../Specs%20&%20ADRs/Ingest%20and%20Team%20Server%20-%202026-10-22.md)

One QASight server that every CI job uploads its test results to, so the whole team sees one history: the dashboard, run details, Compare, trends and flaky tests across every branch and pipeline. CI keeps running its tests the way it does today; one extra step uploads the JUnit XML (or Playwright's JSON report, which keeps retries and flaky tests).

## Start it

Quickest, from any machine with Docker and `pip install qasight`:

```bash
qasight server init --domain qasight.example.com --admin ada   # both optional
cd qasight-server && docker compose up -d
```

[`qasight server init`](../CLI%20Commands/Command%20Reference.md#qasight-server-init) writes the compose file below, a `.env` (mode 600) with a generated `TESTO_API_TOKEN` and Postgres password, and prints what to run next; secrets are never printed. `--domain` adds [Caddy](https://caddyserver.com), which gets the HTTPS certificate by itself (ports 80 and 443 open, DNS pointing at the machine), and keeps the server's own port on `127.0.0.1`. `--admin NAME` turns on [accounts](#accounts) and the server creates that admin on its first start (`TESTO_ADMIN_USER` / `TESTO_ADMIN_PASSWORD` in `.env`), so nobody needs `docker compose exec` for the first user.

Or from a checkout of the repository (`deploy/server/docker-compose.yml`: the `qasight-server` image, which is the API with the web UI built in, plus Postgres):

```bash
export TESTO_API_TOKEN="$(openssl rand -hex 32)"   # keep it: CI needs it to upload
cd deploy/server
docker compose up -d          # pulls ghcr.io/arieleven/qasight-server; add --build to build from the checkout
```

Open <http://localhost:8000> and sign in with the token on the `/login` page. That sets an HttpOnly session cookie (30 days, `SameSite=Strict`), so the UI, report links and the live run stream all work; the cookie is derived from the token, so changing `TESTO_API_TOKEN` signs out everyone who signed in with it ([Rotating the token](#rotating-the-token)). CI and scripts send `Authorization: Bearer $TESTO_API_TOKEN` instead. Signing out only clears that browser's cookie; change the token to sign everyone out. Every write (uploads, starting runs, AI settings) needs one of the two, and so does every read while `TESTO_PROTECT_READS` is on, which the image and the compose file do by default; only the health checks and the login page stay open. Run logs and report files can carry whatever a test printed, so keep reads protected unless the server is on a network you already trust.

| Variable | Default | Meaning |
|----------|---------|---------|
| `TESTO_API_TOKEN` | required | Bearer token for every write. The image binds `0.0.0.0`, so without it `qasight-api` refuses to start unless `TESTO_ALLOW_UNAUTHENTICATED=1` |
| `TESTO_API_TOKEN_PREVIOUS` | unset | The old token, still accepted while CI secrets move to a new `TESTO_API_TOKEN` ([Rotating the token](#rotating-the-token)) |
| `TESTO_SESSION_KEY` | unset | Signs account sign-in cookies and report tickets instead of the token, so a new token keeps people signed in. `qasight server init` generates one |
| `TESTO_RETENTION_DAYS` | unset (`180` from `qasight server init`) | Delete runs and report archives that started more than this many days ago. Overrides `history.retention.days`; `0` keeps everything ([Storage](#storage)) |
| `TESTO_RETENTION_RUNS_PER_CYCLE` | unset | Keep at most this many of each cycle's newest runs and archives. Overrides `history.retention.runs_per_cycle` |
| `TESTO_HISTORY_MAX_FILE_MB` | unset (`100` from `qasight server init`) | Files bigger than this (videos, traces) aren't kept in a run's history copy or report archive. Overrides `history.max_file_mb` |
| `TESTO_LOW_DISK_PCT` | `10` | Below this share of free disk, `/api/v1/health/ready` reports a `disk` warning and the Runs page says the disk is getting full |
| `TESTO_PROTECT_READS` | `1` (set in the image) | `1`: reads need the token or the `/login` cookie too. `0`: anyone who can reach the server can read runs, logs and reports |
| `TESTO_MAX_CONCURRENT_RUNS` | `2` | Runs started from the UI or API that execute at once; the rest wait as `queued` |
| `TESTO_AUTH` | `token` | `token`: everyone shares `TESTO_API_TOKEN`. `accounts`: people sign in with their own username and password, with roles and an audit log ([Accounts](#accounts)) |
| `TESTO_LOGIN_MAX_FAILURES` | `5` | With accounts: failed sign-ins per name in 15 minutes before `/login` answers `429` for a while (four times this per client address) |
| `TESTO_REPORTS_ORIGIN` | unset | e.g. `https://reports.qasight.example.com`: serve report pages from that second hostname instead of the API's ([Reports on their own origin](#reports-on-their-own-origin)) |
| `TESTO_RUNNER` | `host` | `container`: each stage of a run started on the server runs in a fresh container through the runner service ([Runs in throwaway containers](#runs-in-throwaway-containers)) |
| `TESTO_RUN_QUEUE` | `memory` | Where `queued` runs wait. `memory`: a restart drops them. `db`: they are also rows in the database (`queued_run`) and are queued again when the server starts, so a restart or upgrade loses nothing that hadn't started. A run that was already running is not repeated; it shows as failed in the history |
| `TESTO_ALLOWED_HOSTS` | unset | Only matters without a token: extra `Host` names a token-less API answers (it otherwise answers only `localhost`, `127.0.0.1` and `::1`, so a DNS-rebinding page can't read it). With a token, or `TESTO_ALLOW_UNAUTHENTICATED=1`, any name is answered |
| `FORWARDED_ALLOW_IPS` | unset | Behind an HTTPS reverse proxy, the proxy's address, so the session cookie is marked `Secure` |
| `TESTO_ADMIN_USER`, `TESTO_ADMIN_PASSWORD` | unset | With `TESTO_AUTH=accounts` and no account yet: the server creates this admin when it starts. Once any account exists they are ignored, so changing them never resets a password |
| `POSTGRES_PASSWORD` | `testo` | Database password (internal to the compose network) |
| `TESTO_SERVER_PORT` | `8000` | Port on the host |
| `TESTO_SERVER_TAG` | `1` | Image tag to pull |

Run history lives in Postgres (`postgres_data` volume); per-run artifacts and reports in `testo_data` (`/data` in the container). Put the server behind HTTPS (any reverse proxy) before exposing it beyond your network.

## Accounts

The shared token is fine for one person or a small team. When you want to know who did what, or to let some people look without starting runs, turn on accounts:

```bash
export TESTO_AUTH=accounts      # TESTO_API_TOKEN stays set: CI keeps using it
docker compose up -d
docker compose exec server qasight users add alice --role admin
docker compose exec server qasight users add bob --role runner
docker compose exec server qasight users add carol            # viewer
```

People then sign in at `/login` with their username and password. Admins can also leave the username empty and use the token, which is how you get in before any account exists.

| Role | Can |
|------|-----|
| `viewer` | See runs, logs, reports and the live run stream |
| `runner` | Also start and stop runs from the UI or API, upload results and mark failure causes |
| `admin` | Also change the AI provider settings, delete runs from the history and read the audit log |

- Reads always need a session in this mode, whatever `TESTO_PROTECT_READS` says.
- The token is an admin for CI and scripts (`Authorization: Bearer`), and it signs the session cookies: changing it signs everyone out. `qasight users sign-out NAME`, `set-role` and `reset-password` sign one person out.
- Every write and every sign-in attempt is recorded with its user and status. Admins read the latest at `GET /api/v1/audit?limit=100`.
- The UI shows who is signed in, with a Sign out button.
- A `runner` can start any command the API can run, as the server's own user, so give that role to people you'd trust with a shell on the server, or run stages in [throwaway containers](#runs-in-throwaway-containers).

After five wrong passwords for one name within 15 minutes (`TESTO_LOGIN_MAX_FAILURES`), that name has to wait before trying again, even with the right password; the wait is in the audit log as `429`.

Set `TESTO_AUTH=token` (or unset it) to go back; the accounts stay in the database for next time.

## Reports for managers

The Report page (`/report`) summarises a day, week, month or quarter for people who don't read every run, and its Download menu gives the same report as PowerPoint, Excel, HTML, CSV or JSON from the server image as built. PDF and PNG need Chromium in the image, about 400 MB more, so they are opt-in: build with `--build-arg TESTO_REPORT_PDF=1` (`docker build -f Dockerfile.qasight-server --build-arg TESTO_REPORT_PDF=1 .`). Without it the page's "Save as PDF" button still works: the browser prints the page. To email the report every Monday, run [`qasight notify --digest week --to email`](../CLI%20Commands/Command%20Reference.md#qasight-notify) from a scheduled CI job or cron against the same database.

## Reports on their own origin

Allure and native reports are web pages that run script. By default they are served from the server's own address, so a script in a report would run with the viewer's session. To keep them apart, point a second hostname at the same server (a DNS name and a reverse-proxy rule) and set:

```bash
export TESTO_REPORTS_ORIGIN=https://reports.qasight.example.com
```

Report links in the UI keep working: the server checks that the viewer may read, then redirects to the reports host with a 60-second signed ticket, which that host trades for its own cookie. The reports host serves nothing but `/history`, its cookie can only read reports, and the API refuses writes that come from the reports origin. Worth turning on when the server is reachable by people you don't fully trust.

## Runs in throwaway containers

Runs started from the server's UI or API (not uploads) normally run inside the server's own container, as its user, next to its token and database settings. To run each stage in a fresh container instead, add the runner service:

```bash
export TESTO_RUNNER_TOKEN="$(openssl rand -hex 32)"
export DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
docker compose -f docker-compose.yml -f docker-compose.runner.yml up -d    # add --build from a checkout
```

`deploy/server/docker-compose.runner.yml` sets `TESTO_RUNNER=container` on the server and adds a `runner` service, the only container with the Docker socket. For every stage it runs `docker run --rm` with:

- the `testo_repos` (`/repos`) and `testo_artifacts` (`/data/artifacts`) volumes, at the same paths as in the server; put the repositories your cycles test under `/repos`,
- only the stage's own variables (its `env:` and what QASight sets), never the server's token or database URL; `${env:...}` in a stage's `args` and `env:` can't read them either (any `TESTO_*`, `DATABASE_URL`, `POSTGRES_*` or `*_API_KEY` / `*_TOKEN` / `*_SECRET` / `*_PASSWORD` / `*_DB_URL` variable of the server resolves as unset),
- no Linux capabilities, no privilege escalation and at most 512 processes,
- the commands in `TESTO_RUNNER_ALLOWED_COMMANDS` only.

| Variable (on `runner`) | Default | Meaning |
|----------|---------|---------|
| `TESTO_RUNNER_TOKEN` | required | Shared with the server, which sends it with every stage |
| `TESTO_RUNNER_MOUNTS` | required | `volume-or-path:target[:ro]`, comma separated. A stage's directory must be inside one |
| `TESTO_RUNNER_IMAGE` | `ghcr.io/arieleven/qasight-runner:1` | Image stages run in (Python, pytest, Behave, BehaveX). Build your own `FROM` it for other tools |
| `TESTO_RUNNER_ALLOWED_COMMANDS` | `python,python3,pytest,behave,behavex,node,npm,npx` | Tools a stage may start, by name. `*` allows any, still inside the container |
| `TESTO_RUNNER_NETWORK` | Docker's default | `none` cuts stages off the network; or the name of a network your tests need |
| `TESTO_RUNNER_MEMORY`, `TESTO_RUNNER_CPUS` | unset | Per-stage limits, e.g. `2g` and `1.5` |
| `TESTO_RUNNER_PIDS_LIMIT` | `512` | Processes per stage |
| `TESTO_RUNNER_USER` | `10001:10001` | User stages run as (the images' `qasight`, so files stay readable by the server) |
| `TESTO_RUNNER_MAX_RUNS` | `4` | Stages at once; more wait for a slot |

A `per: stage` resource pool with `on_take` / `on_check` / `on_release` hooks is refused in this mode, because those hooks would run in the server's process; use `per: test` ([Shared Resource Pools](Shared%20Resource%20Pools.md)). A timeout stops the container the same way it stops a local process (the stage gets `124`), and if the server goes away mid-run the runner stops the container. Unset `TESTO_RUNNER` (or drop the second compose file) to go back. The runner service has no published port, but the Docker socket is root on the host, so keep it on the compose network; rootless Docker or Podman via `DOCKER_HOST` narrows that further. Why it is built this way: [Throwaway Stage Containers - 2026-10-24](../Specs%20&%20ADRs/Throwaway%20Stage%20Containers%20-%202026-10-24.md).

## Shared resource pools

With `TESTO_SERVER_URL` and `TESTO_API_TOKEN` set on every laptop and CI agent, `qasight lease` takes keys (VMs, devices, test accounts) through the server, so parallel workers of different people never get the same one. The leases live in the server's database; its Resources page shows who holds what. See [Shared Resource Pools](Shared%20Resource%20Pools.md).

## Upload from CI

After the tests, in any CI system:

```bash
pip install qasight
qasight ingest 'reports/**/*.xml' --cycle web-unit --server "$TESTO_SERVER_URL" --token "$TESTO_API_TOKEN"
```

GitHub Actions, with the Action (it keeps the job summary and pull request comment too):

```yaml
      - uses: arieleven/qasight@v1
        with:
          server-url: https://qasight.example.com
          server-token: ${{ secrets.TESTO_API_TOKEN }}
```

`qasight init --server https://qasight.example.com` writes those lines and, with the GitHub CLI and `$TESTO_API_TOKEN` set, stores the secret. Or as a plain step:

```yaml
      - run: npx jest --ci --json --outputFile=jest.json   # your tests, plus Jest's own report
      - if: always()            # record failing runs too
        run: |
          pip install qasight
          qasight ingest jest.json --cycle web-unit
        env:
          TESTO_SERVER_URL: https://qasight.example.com
          TESTO_API_TOKEN: ${{ secrets.TESTO_API_TOKEN }}
```

The upload carries the job's CI provenance (provider, pipeline, job, commit, branch and repository for GitHub Actions, GitLab CI, Buildkite, CircleCI, Jenkins and Azure Pipelines). The step exits `1` when a test failed, like the test step itself, and `3` when the server can't be reached or refuses the token. Use the same `--cycle` name for the same suite every time; that name is what Compare, trends and `qasight flaky` group by.

## How it works

`POST /api/v1/ingest` (multipart: `files`, `cycle`, optional `stage`, `tier` and provenance fields) writes the files to a scratch directory and records them through `CycleRunService` as a one-stage `junit` cycle, the same path as a local `qasight ingest` and `qasight run`. Nothing in an upload is executed. Limits: 200 files, 20 MB per file, 100 MB per upload. The response has the `run_id`, the counts, the exit code and a link to the run page.

The database schema is upgraded in place when the server starts: the API runs the Alembic revisions in `qasight/repository/migrations/` against its Postgres (or SQLite), adopting a database from 1.5.0 or earlier without dropping anything. Upgrade the image, restart, done; see [Repository Pattern § Schema migrations](../Specs%20&%20ADRs/Repository%20Pattern%20-%20Database-Agnostic%20Refactor.md#schema-migrations-2026-11-01).

If the server or a `qasight run` on its machine dies in the middle of a run (`kill -9`, out of memory, the machine going down), the run is recorded as failed with the reason "stopped without finishing" when the server starts again, or when that cycle next runs. Its test results are lost; the run no longer disappears without a trace.

The image sets `TESTO_UI_DIR=/app/ui`, which makes the API serve the built React UI at `/` (unknown paths fall back to `index.html`, so `/runs/<id>` loads directly). With a token set, the UI may write to its own origin; without one, only the `TESTO_CORS_ORIGINS` allow-list may, and `*` in that list counts only when a token is set.

## Storage

History grows with every run: a row per run and per test in Postgres, a copy of each run's result files under `/data/static/history/`, and a zip of the cycle folder per server-started run (a report archive, `qasight report list`). Without limits it is kept forever.

- **Limits.** `TESTO_RETENTION_DAYS` and `TESTO_RETENTION_RUNS_PER_CYCLE` (or a `history:` section in the server's `qasight.yaml`; the variables win) delete expired runs, their per-test rows, their file copies and old report archives at startup and every 6 hours. `TESTO_HISTORY_MAX_FILE_MB` leaves big files (videos, traces) out of the history copies; the run records which ones. `qasight server init` sets 180 days and 100 MB. The same pruning runs by hand as [`qasight clean --history`](../CLI%20Commands/Command%20Reference.md#qasight-clean---history), and single archives go with `qasight report delete --id`.
- **Seeing it.** The Runs page ends with a storage line: runs, database size, file size, report archives and free disk (`GET /api/v1/storage`, measured at most every 5 minutes). It warns when free space drops under `TESTO_LOW_DISK_PCT`. `/api/v1/health/ready` reports a `disk` check: `warning` (still 200) when low, `degraded` (503) under 200 MB free. `qasight doctor` prints the same numbers.
- **When a copy fails** (a full disk), the run is still recorded, the half copy is removed and the run notes `snapshot_error`, so a run missing from Compare or flaky detection says why.

## Rotating the token

`TESTO_API_TOKEN` is what CI uploads with and an admin login. To replace it (someone who knew it left, a CI log leaked it):

1. Set the new value as `TESTO_API_TOKEN` and the old one as `TESTO_API_TOKEN_PREVIOUS` in `.env`, then `docker compose up -d`. Both are accepted.
2. Update the CI secret in every pipeline.
3. Remove `TESTO_API_TOKEN_PREVIOUS` and restart. The old token stops working.

With `TESTO_SESSION_KEY` set (servers made by `qasight server init` 1.6 or later have one), account sign-ins are signed with it, so people stay signed in through the rotation. Without it, they stay signed in only while the previous token is set. Changing `TESTO_SESSION_KEY` itself signs every account out. To remove one person, use `qasight users remove` ([Accounts](#accounts)); that needs no token change unless they also knew the token.

## Backups

The compose file's `backup` service runs [`qasight backup`](../CLI%20Commands/Command%20Reference.md#qasight-backup--qasight-restore) when it starts and then every `TESTO_BACKUP_EVERY_S` seconds (default a day), keeping the newest `TESTO_BACKUP_KEEP` files (default `7`) in their own `testo_backups` volume at `/backups`, so backups never fill the disk the server writes to; put that volume on another disk if you can. A backup cut short leaves a `.partial` file, which the next round removes. Each backup is the whole history: every table and each run's stored results. Copy them off the machine, since a backup on the same disk doesn't survive losing it:

```bash
docker compose cp backup:/backups ./testo-backups
docker compose run --rm --entrypoint qasight backup restore /backups/testo-<time>.tar.gz --replace --yes
```

Compose files from before 1.6 kept backups in `/data/backups`; after updating the compose file (it needs the 1.6 image, which owns `/backups`), copy any you want to keep out of that folder, then delete it.

They hold password hashes when [accounts](#accounts) are on; keep them as private as the database. Don't want it: `docker compose up -d --scale backup=0`.

## Limits today

- Cycles that exist only as uploads aren't in the server's `qasight.yaml`, so they can't be started from the UI. They still get a cycle page, and show on the Cycles page, Dashboard, Runs and Compare.
- No projects: everyone who can read sees every cycle. [Accounts](#accounts) give per-person roles, not per-project ones.
- Starting runs from the server's UI only makes sense for cycles defined in the server's config. Without [throwaway containers](#runs-in-throwaway-containers) they run inside the server container. The team server is mainly meant for uploads.

CI checks all of this end to end: the `team_server` job in `ci.yml` builds the image, starts the compose file and uploads with `qasight ingest --server` (`scripts/team_server_smoke.sh`).
