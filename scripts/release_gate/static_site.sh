#!/usr/bin/env bash
# Release gate: build the read-only demo site (as pages-demo.yml does) into $1,
# from one real run of the sample cycle, for Lighthouse to audit.
#
#   bash scripts/release_gate/static_site.sh public
set -euo pipefail

out="${1:?usage: static_site.sh OUT_DIR}"
site_url="${SITE_URL:-http://localhost}"
work="$(mktemp -d)"
export DATABASE_URL="sqlite:///${work}/gate.db"

qasight run --cycle sample-pytests --ci > "${work}/run.ndjson" || true
python scripts/export_static_site.py --out "${out}" --site-url "${site_url}"
(
  cd frontend
  VITE_BASE=/ VITE_STATIC_DATA_BASE=/data VITE_API_BASE_URL="${site_url}" npm run build
)
cp -r frontend/dist/. "${out}/"
cp "${out}/index.html" "${out}/404.html"
