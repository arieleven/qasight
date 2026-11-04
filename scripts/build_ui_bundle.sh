#!/usr/bin/env bash
# Build the React UI to call the API on its own origin and copy it into
# qasight_api/ui_dist/, so the next `python -m build` puts it in the wheel and
# `qasight ui` can serve it. Run from the repository root; needs Node 22 and npm.
# Usage: scripts/build_ui_bundle.sh
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
npm --prefix "$root/frontend" ci --no-audit --no-fund
# A relative base (VITE_BASE=./) lets `qasight export-site` publish the same build under any subpath.
VITE_API_BASE_URL="" VITE_BASE=./ npm --prefix "$root/frontend" run build
rm -rf "$root/qasight_api/ui_dist"
cp -r "$root/frontend/dist" "$root/qasight_api/ui_dist"
test -f "$root/qasight_api/ui_dist/index.html"
echo "UI bundled into qasight_api/ui_dist/"
