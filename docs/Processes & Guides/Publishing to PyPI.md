---
type: guide
status: current
created: 2026-07-01
updated: 2026-11-12
---

# Publishing to PyPI

[Index](../Index.md) · [Publishing Docker Images](Publishing%20Docker%20Images.md) · [Publishing to Artifactory](Publishing%20to%20Artifactory.md)

## Overview

`qasight` is published to PyPI via GitHub Actions using trusted publishing (OIDC). No API tokens are stored in repository secrets.

Until 1.5.0 the package was `testo-core`. From 1.6.0 every release also publishes a `testo-core` of the same version from [`integrations/testo-core/`](../../integrations/testo-core/README.md): it only depends on `qasight==<version>`, writes the old `testo` commands again and keeps `import testo_core` / `import testo_api` working, so `pip install -U testo-core` keeps upgrading people who never switch. `tests/contract/ci/test_testo_core_compat.py` keeps its version and extras in step, and `publish.yml` refuses a release where they differ. See [Rename to QASight](../Specs%20%26%20ADRs/Rename%20to%20QASight%20-%202026-11-04.md).

## Prerequisites

1. **PyPI account**: register at https://pypi.org (2FA is required to publish). There is no Test PyPI step: the build, metadata and clean-install checks run before the upload instead.
2. **Pending trusted publisher** on PyPI, which reserves the name before the first upload: account menu → "Publishing" → "Add a new pending publisher" → GitHub, with
   - PyPI project name: `qasight` (and the existing project `testo-core`, whose publisher must name the same repository and workflow)
   - Owner: `arieleven`
   - Repository: `qasight` (earlier `taltal-beep/testosterone` and `taltal-beep/unified-quality-orchestration-reporting-dashboard`; a publisher registered under an old owner or repository name must be updated after a rename)
   - Workflow: `publish.yml`
   - Environment: `pypi`
3. **GitHub environment**: create `pypi` under the repository's Settings → Environments. Adding yourself as a required reviewer makes the upload wait for a click.

## Publishing flow

1. Merge the version bump. Its pull request and its commit on `main` run the release gate (`release-gate.yml`, [Release Gate](../Specs%20&%20ADRs/Release%20Gate%20-%202026-11-12.md)); wait for the run on `main` to pass, then create a GitHub Release with a semver tag matching `pyproject.toml` (e.g. `v1.1.1` for `version = "1.1.1"`) on that commit.
2. The `publish.yml` workflow triggers automatically. It first checks that a `release gate` job passed on the tag's commit and stops if not. Its publish job then waits for the `pypi` environment's reviewer, then checks out the tag, builds the web UI into `qasight_api/ui_dist/` (`scripts/build_ui_bundle.sh`, so `qasight ui` works from the wheel), builds the package, checks the tag matches the package version, runs `twine check --strict`, installs the wheel with no extras and runs a cycle (`scripts/package_smoke.sh`), publishes with `pypa/gh-action-pypi-publish` over OIDC (no API token), and verifies `pip install`, retrying for up to five minutes while PyPI's index catches up with the upload.
3. To publish an existing tag whose automatic run failed (for example after fixing the trusted publisher), run the workflow by hand from the Actions tab with that tag as input. PyPI refuses a version that was already uploaded.
4. The same Release also triggers `docker-publish.yml` (runner image to GHCR; the first push creates a private package, so set its visibility to public once) and `artifactory-publish.yml` (skipped while `ARTIFACTORY_URL` is unset).
5. To list the GitHub Action on the Marketplace, tick "Publish this Action to the GitHub Marketplace" on the Release form (category Testing). It lists the root `action.yml`. The account owner accepts the Marketplace Developer Agreement once, and publishing needs two-factor login; after that, each Release with the box ticked updates the listing.

The `package` job in `ci.yml` runs the UI bundle, the build and the clean-install check (which also fails if the wheel has no UI) on every pull request, so a release should not be the first time they run.

## Current state

Version 1.1.1 is the first release meant for PyPI; 1.1.0 was never published and its wheel crashed on `qasight run` without the `[db]` extra (see `CHANGELOG.md`). It is on PyPI since 2026-10-19, as `testo-core` until 1.5.0 (https://pypi.org/project/testo-core/) and as `qasight` from 1.6.0 (https://pypi.org/project/qasight/). 1.2.0 was released on 2026-10-24, 1.3.0 on 2026-10-25, 1.4.0 on 2026-10-31 and 1.5.0 on 2026-11-01. 1.6.0, the first release named `qasight`, is the version on `main` and waits for its Release. The `v1.1.1` Release's automatic run stopped at the old Test PyPI step, so 1.1.1 was uploaded with a manual run of the workflow. The moving `v1` tag the GitHub Action docs refer to ([CI-CD Pipeline Setup § Versioning policy](CI-CD%20Pipeline%20Setup.md#versioning-policy)) is moved by `move-major-tag.yml` on each stable release.

## pytest-qasight

[`integrations/pytest-qasight/`](https://github.com/arieleven/qasight/tree/main/integrations/pytest-qasight) is a second, tiny PyPI project, `pytest-qasight`. It has no code of its own: it depends on `qasight` (whose `pytest11` entry point provides `pytest --qasight`) and exists because pytest's [plugin list](https://docs.pytest.org/en/stable/reference/plugin_list.html) only picks up PyPI names that start with `pytest-`. It registers no entry point, since a second registration of the same plugin would stop pytest; `tests/contract/ci/test_pytest_qasight_package.py` checks that.

It is released by hand, only when its own version changes (`version` in its `pyproject.toml` and `__version__` in `pytest_qasight/__init__.py`): run **Publish pytest-qasight to PyPI** (`publish-pytest-qasight.yml`) from the Actions tab. It builds, runs `twine check`, installs the wheel with `qasight` from PyPI and records a one-test `pytest --qasight` run, then uploads. Before the first run the PyPI account owner adds a pending publisher like the one above, with project `pytest-qasight`, workflow `publish-pytest-qasight.yml` and environment `pypi`.

## Version management

- Version is set in `pyproject.toml` under `[project] version`.
- Bump version before creating the release tag.
- The release tag should match the version (e.g. `v1.0.0` → `version = "1.0.0"`).

## Verifying a release

```bash
pip install qasight==1.6.0
qasight --version
qasight run --help
```

## Troubleshooting

- **OIDC auth failure**: Verify trusted publisher configuration matches the workflow filename and GitHub environment name exactly.
- **No passing release gate**: the tag's commit has no green `release gate` job. Run `release-gate.yml` on that commit from the Actions tab, then re-run `publish.yml` by hand with the tag. `skip_gate` on a by-hand run publishes without the gate.
- **Tag does not match**: the job stops when the tag (minus its `v`) differs from `[project] version`. Fix the version or the tag and create a new Release.
- **`twine check` failure**: Check `pyproject.toml` for missing metadata fields (`description`, `readme`, `requires-python`).
- **"Verify install from PyPI" red after a successful upload**: the package is published; the index was slower than the retry window. Check `pip install qasight==X.Y.Z` by hand, and if it installs there is nothing to fix (v1.2.0 hit this before the retry was added).
- **Version conflict**: PyPI does not allow re-uploading the same version. Bump the version and create a new release.

## Related

- [CI-CD Pipeline Setup](CI-CD%20Pipeline%20Setup.md) — CI workflow overview
- Workflow file: `.github/workflows/publish.yml`
