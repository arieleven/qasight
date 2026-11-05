---
type: spec
status: current
created: 2026-11-05
updated: 2026-11-05
---

# QASight Logo - 2026-11-05

[Specs & ADRs](README.md) · [Rename to QASight - 2026-11-04](Rename%20to%20QASight%20-%202026-11-04.md)

## Context

After the [rename](Rename%20to%20QASight%20-%202026-11-04.md) the web UI already showed an eye, but the README had no logo, the docs site logo and favicon (`docs/assets/logo.png`) and the GitHub social preview still showed the Testosterone arm and syringe, and the README screenshots still said TESTO. PyPI shows the README, so it showed no QASight logo either. An Allure-style ring of status colours for the iris was tried and dropped; Taltal chose to keep the eye as the UI draws it.

## Decision

The logo is the web UI's eye (`frontend/src/components/mascot/EyeLogo.tsx`) with the green iris it shows on a healthy Project health page (`success-400`, `#34d399`), next to the name QASight in Plus Jakarta Sans bold. The white of the eye gets a thin `ink-200` outline on light backgrounds.

- `scripts/render_logo.js` draws the mark and renders every file into `docs/assets/`: `qasight-mark.svg`, `qasight-logo-dark.png` and `qasight-logo-light.png` (the README picks one by colour scheme), `logo.png` (docs site logo and favicon, see `mkdocs.yml`) and `qasight-social-preview.png` (1280×640, uploaded by hand under the repository's Settings → Social preview).
- The README logo uses relative image paths, which the `fancy-pypi-readme` substitutions in `pyproject.toml` point at raw GitHub URLs for PyPI, so PyPI shows it from the next release on.

## Consequences

- If the eye in `EyeLogo.tsx` changes, change it in `scripts/render_logo.js` too and rerun the script.
- The README screenshots in `docs/assets/screenshots/` were retaken from a local build of the static demo (`scripts/export_static_site.py` plus `npm run build` in static mode) so they show the new name.
- The social preview is a repository setting, not a file GitHub reads from the tree, so changing it is a manual step.
