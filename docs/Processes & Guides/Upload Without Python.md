---
type: guide
status: current
created: 2026-11-20
updated: 2026-11-20
---

# Upload Without Python

[Index](../Index.md) · [Team Server](Team%20Server.md) · [Go Uploader - 2026-11-20](../Specs%20&%20ADRs/Go%20Uploader%20-%202026-11-20.md)

`qasight-upload` sends a CI job's test result files to a [team server](Team%20Server.md), like `qasight ingest --server`, as one small program with nothing to install: no Python, no pip, no Docker. Use it on CI images without Python (Node, Go, Java, .NET, a bare Alpine) or where installing packages is slow or not allowed. Where Python is already there, `pip install qasight` and `qasight ingest` do the same.

It only uploads. Recording, flaky detection, Compare and the UI stay on the server.

## Get it

Each QASight release after 1.7.0 has the binaries attached, for Linux, macOS and Windows on amd64 and arm64, with a `qasight-upload-SHA256SUMS` file and a signed build provenance attestation:

```bash
curl -fsSL -o qasight-upload \
  https://github.com/arieleven/qasight/releases/latest/download/qasight-upload-linux-amd64
chmod +x qasight-upload
```

The file names are `qasight-upload-<os>-<arch>` (`linux`, `darwin`, `windows` with `.exe`; `amd64`, `arm64`). Pin a version with `releases/download/v<version>/...` instead of `latest`. To check a download: `sha256sum -c --ignore-missing qasight-upload-SHA256SUMS`, or `gh attestation verify qasight-upload --repo arieleven/qasight`. On a network with no internet, carry the file in with the [closed network bundle](Closed%20Network%20Install.md) or on its own. From a checkout: `cd uploader && go build -o qasight-upload .`

## Use it

```bash
export TESTO_SERVER_URL=https://qasight.example.com
export TESTO_API_TOKEN=...            # a CI secret
./qasight-upload --cycle web-unit 'reports/**/*.xml'
```

Files, folders and globs work as they do for [`qasight ingest`](../CLI%20Commands/Command%20Reference.md#qasight-ingest): JUnit XML, Playwright, Jest and Vitest JSON, `.trx`, `go test -json` output, Robot Framework `output.xml`, or an `allure-results` folder (the files directly in it). Quote a glob and `qasight-upload` expands it, with `**` for any depth, on Windows too. The server reads what each file is from its content.

| Flag | Default | Meaning |
|------|---------|---------|
| `--server URL` | `$TESTO_SERVER_URL` | The team server |
| `--token TOKEN` | `$TESTO_API_TOKEN` | Its API token (never printed) |
| `--cycle NAME` | the current folder's name | Cycle to record the run under; keep it the same for the same suite |
| `--stage NAME`, `--tier TIER` | `junit`, `unit` | As for `qasight ingest` |
| `--env KEY=VALUE` | `$TESTO_ENV`, else `os=<runner OS>` in CI | What the run tested on, repeatable |
| `--release NAME` | `$TESTO_RELEASE`, else the git tag CI builds | The release it tested |
| `--repository NAME` | the CI provider's variables | `owner/name` |
| `--json` (or `--ci`) | off | One JSON object on stdout instead of text |
| `--retries N` | `3` | Retries when the server can't be reached or is busy |
| `--timeout DURATION` | `2m` | Time allowed for each attempt |
| `--dry-run` | off | Print the files and fields it would send, and send nothing |

The upload carries the job's CI provenance (provider, pipeline, job, commit, branch, target branch and repository) for GitHub Actions, GitLab CI, Buildkite, CircleCI, Jenkins and Azure Pipelines, by the same rules as `qasight ingest`, and `TESTO_AUTO_METADATA=0` turns off the OS and tag defaults the same way. `--coverage` is not supported; use `qasight ingest --coverage` for that. `HTTPS_PROXY` and `SSL_CERT_FILE` (a company CA) are honoured.

### Exit codes and output

| Code | Meaning |
|------|---------|
| `0` | Recorded; every test passed or was skipped |
| `1` | Recorded; a test failed (like the test step itself) |
| `2` | Bad input: a flag, no matching file, a file over the server's limits (200 files, 20 MB each, 100 MB in all), or the server refused the upload (`400`, `413`, `422`) |
| `3` | The server could not be reached, refused the token (`401`, `403`), or failed |
| `4` | Unexpected error |

With `--json`, stdout is one line: `{"event": "ingest_uploaded", "run_id": ..., "cycle": ..., "exit_code": ..., "tests": ..., "passed": ..., "failed": ..., "skipped": ..., "run_url": ..., "server": ...}`, the same event `qasight ingest --server --ci` prints, or `{"event": "error", "code": "invalid_input" | "infra_failure", "message": ...}`.

### Retries

A retry never records a run twice: `qasight-upload` sends again only when the request did not reach the server (connection refused, DNS, TLS) or the server answered `429`, `502` or `503`, which mean it did nothing. It waits 2, 4, 8 seconds (plus a little jitter) or what `Retry-After` asks, up to a minute. A connection lost after the files were sent, or a `504`, is not retried, because the server may have recorded the run; it exits `3`.

## In CI

GitHub Actions on a runner without Python (the [QASight Action](CI-CD%20Pipeline%20Setup.md) is still the richer choice where Python is fine: job summary, PR comment):

```yaml
      - if: always()            # record failing runs too
        run: |
          curl -fsSL -o qasight-upload https://github.com/arieleven/qasight/releases/latest/download/qasight-upload-linux-amd64
          chmod +x qasight-upload && ./qasight-upload --cycle web-unit 'reports/**/*.xml'
        env:
          TESTO_SERVER_URL: https://qasight.example.com
          TESTO_API_TOKEN: ${{ secrets.TESTO_API_TOKEN }}
```

GitLab CI, in any image with `curl` (or `wget -qO`):

```yaml
upload-results:
  stage: .post
  when: always
  script:
    - curl -fsSL -o qasight-upload https://github.com/arieleven/qasight/releases/latest/download/qasight-upload-linux-amd64
    - chmod +x qasight-upload && ./qasight-upload --cycle web-unit 'reports/**/*.xml'
```

`TESTO_SERVER_URL` and `TESTO_API_TOKEN` go in the project's CI/CD variables (masked). Windows runners take `qasight-upload-windows-amd64.exe`.

## How it is built and tested

The code is in [`uploader/`](https://github.com/arieleven/qasight/tree/main/uploader): Go's standard library only, no third-party modules. It sends the same `POST /api/v1/ingest` request as the Python client (`qasight/services/ingest_client.py`), so nothing on the server changed for it. `.github/workflows/uploader.yml` runs `go vet`, the tests with the race detector and a build for every platform, and `scripts/upload_binary_smoke.sh` uploads with the built binary to a real `qasight-api` from the same commit, so a change to the ingest API that would break the uploader fails that pull request. On a published release the same workflow builds the binaries (static, `CGO_ENABLED=0`, version from the tag), attests them and attaches them to the release.
