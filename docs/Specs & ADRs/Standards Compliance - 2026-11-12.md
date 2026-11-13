---
type: spec
status: current
created: 2026-11-12
updated: 2026-11-12
related: "Release Gate - 2026-11-12"
---

# Standards Compliance - 2026-11-12

[Index](../Index.md) · On 2026-11-12 Taltal asked which published standards QASight should meet, and chose all five that fit an open source test tool with a web UI. This note records where QASight stands against each one, what changed to close gaps, and what is left. The checks that run on every pull request and before each release are in [Release Gate](Release%20Gate%20-%202026-11-12.md).

| Standard | What it covers | Where QASight stands |
|---|---|---|
| [OpenSSF Best Practices](https://www.bestpractices.dev/) (passing) | Project hygiene: licence, docs, reporting, tests, analysis, releases | Every criterion met (answers below). The badge needs Taltal to submit the form |
| [SLSA](https://slsa.dev/) build level 2 and SBOMs | Supply chain: signed provenance, what a release contains | PyPI and the Docker images carry signed provenance; each release gets CycloneDX SBOMs |
| [OWASP ASVS 4.0](https://owasp.org/www-project-application-security-verification-standard/) level 1 | Web app security: auth, sessions, access, input, headers | Met for the server; two missing headers added |
| [WCAG 2.1 AA](https://www.w3.org/TR/WCAG21/) | Accessibility of the web UI | axe checks every page in CI; Lighthouse checks contrast on the demo |
| [ISO/IEC 25010](https://iso25000.com/index.php/en/iso-25000-standards/iso-25010) | Software product quality model (self-assessment, no certificate) | Assessed below; main gaps are portability of the runner and usability research |

## OpenSSF Best Practices: the answers

To get the badge, sign in at [bestpractices.dev](https://www.bestpractices.dev/) with GitHub, add `https://github.com/arieleven/qasight`, and answer with the evidence below. Then add the badge it gives to the README.

| Area | Criterion | Answer and evidence |
|---|---|---|
| Basics | Description, how to get it, how to contribute | [README](../../README.md), [CONTRIBUTING](../../CONTRIBUTING.md), [Getting Started](../Getting%20Started.md) |
| Basics | FLOSS licence, in a standard place | MIT, [LICENSE](../../LICENSE) |
| Basics | Documentation of the interface | [Command Reference](../CLI%20Commands/Command%20Reference.md), OpenAPI at `/docs`, the docs site |
| Basics | HTTPS site, discussion forum, English | GitHub (HTTPS), Issues and Discussions, English |
| Change control | Public version control, unique release ids, release notes | Git on GitHub, SemVer tags, [CHANGELOG](../../CHANGELOG.md) and GitHub Releases |
| Reporting | Bug reporting process, responses | Issue templates (`.github/ISSUE_TEMPLATE/`); issues are answered |
| Reporting | Private vulnerability reporting, response within 14 days | [SECURITY.md](../../SECURITY.md): GitHub private advisories, acknowledgement within 7 days |
| Quality | Working build, automated test suite, tests for new features | `python -m build`, pytest in CI ([CI-CD Pipeline Setup](../Processes%20&%20Guides/CI-CD%20Pipeline%20Setup.md)), "tests for every change" in CONTRIBUTING, coverage gate at 80% (`pytest.ini`) |
| Quality | Compiler warnings, linters | Ruff, mypy, ESLint and `tsc` on every pull request |
| Security | Secure development knowledge, no broken crypto | scrypt for passwords, `hmac.compare_digest` for tokens, TLS left to the proxy |
| Security | Delivery against MITM | PyPI and GHCR over HTTPS, signed provenance (below) |
| Security | Publicly known vulnerabilities fixed within 60 days | pip-audit and npm audit on every pull request, Dependabot |
| Analysis | Static analysis | Bandit, gitleaks, zizmor and Ruff security rules in CI; Semgrep in the scorecard |
| Analysis | Dynamic analysis | Schemathesis, OWASP ZAP and importer fuzzing in the release gate |

## Supply chain: SLSA and SBOMs

- **PyPI.** `pypa/gh-action-pypi-publish` with trusted publishing uploads signed PEP 740 attestations (SLSA provenance) next to each file. pip and PyPI show them; no key is stored anywhere.
- **Docker.** `docker-publish.yml` pushes each image with `provenance: mode=max` (a signed SLSA provenance attestation) and `sbom: true` (an SPDX SBOM). Read them with `docker buildx imagetools inspect ghcr.io/arieleven/qasight-server:<tag> --format '{{json .SBOM}}'`.
- **Release SBOMs.** The `sbom` job in `publish.yml` attaches `qasight-<version>-python.cdx.json` (every package `pip install "qasight[api]"` brings, by `cyclonedx-py`) and `qasight-<version>-ui.cdx.json` (the npm packages bundled into the UI, by `npm sbom`) to the GitHub release.

## OWASP ASVS level 1

| Chapter | Status | Evidence |
|---|---|---|
| V2 Authentication | Met | Passwords at least 10 characters, scrypt (`qasight/accounts.py`); login throttling (`qasight_api/login_throttle.py`); OIDC single sign-on ([Team Server](../Processes%20&%20Guides/Team%20Server.md#single-sign-on)) |
| V3 Sessions | Met | Session cookie `HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS, fixed lifetime (`qasight_api/routes/auth.py`); logout clears it |
| V4 Access control | Met | Writes need a token or an account role; `TESTO_PROTECT_READS` for reads; foreign-origin logins refused (`qasight_api/security.py`) |
| V5 Validation | Met | Pydantic models on every route; XML through `defusedxml`; config paths limited to config file names; Schemathesis in the release gate |
| V7 Errors and logging | Met | JSON errors without stack traces; an audit log of writes (`audit_write`) |
| V8 Data protection | Met | No secrets in responses or logs; webhook URLs never in error text |
| V9 Communications | Met with a proxy | TLS ends at the proxy or Caddy (`qasight server init`); HSTS sent over HTTPS (added 2026-11-12) |
| V12 Files | Met | Report attachments served with `Content-Security-Policy: sandbox` |
| V13 API | Met | OpenAPI schema, JSON only, 422 for bad bodies |
| V14 Configuration | Met | CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` (added 2026-11-12); pinned dependencies and actions |

The two headers added on 2026-11-12 are `Referrer-Policy: strict-origin-when-cross-origin` on every response, and `Strict-Transport-Security: max-age=31536000` when the request came over HTTPS (directly or through `X-Forwarded-Proto`). `tests/contract/api/test_security_contract.py` covers both.

## WCAG 2.1 AA

- `frontend/src/a11y/wcag.test.tsx` runs axe-core with the WCAG 2.1 A and AA rules on every page of the app (the shell, navigation, and each page as it renders with no data) as part of the frontend tests on every pull request.
- Colour contrast needs real layout, which jsdom doesn't have, so axe skips it there. Lighthouse checks it, with the rest of accessibility, on the demo site in the release gate (`scripts/release_gate/lighthouserc.json`).
- Not covered by automation: screen reader walk-throughs and pages full of data. Check those by hand before a major UI change.

## ISO/IEC 25010 self-assessment

| Characteristic | Evidence | Gap |
|---|---|---|
| Functional suitability | Runs, imports 10+ result formats, flaky detection, compare, releases | None known |
| Performance efficiency | Indexed run queries, Locust load test in the release gate | Large histories (100k runs) not benchmarked |
| Compatibility | Python 3.12 to 3.14, SQLite and Postgres, any CI through `qasight ci publish` | None known |
| Usability | `qasight init`, zero-config Action, axe and Lighthouse checks | No usability study with real users yet |
| Reliability | Run lock, recovery of unfinished runs, backups, mutation testing | Single server replica for scheduled sends |
| Security | ASVS level 1 above, the release gate | No external penetration test |
| Maintainability | One engine through `CycleRunService`, typed code, 80% coverage gate, this vault | None known |
| Portability | pip, uvx, Docker images for amd64 and arm64, Helm chart | Windows runners less tested than Linux |

## Consequences

- A release now carries SBOMs and provenance, which is what security reviews in larger companies ask for first.
- The OpenSSF badge is one form away. Taltal submits it, and the README gets the badge.
- An accessibility regression on any page fails the frontend tests.
