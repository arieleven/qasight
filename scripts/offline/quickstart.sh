#!/usr/bin/env bash
# Same-day QASight on a closed network: one command from the unpacked bundle to a team
# server that records every GitLab pipeline's test report, with no change to any job.
#
#   cp site.env.example site.env && vi site.env    # before you go in
#   ./quickstart.sh                                 # inside; asks for a GitLab read_api token
#   ./quickstart.sh --check                         # only test GitLab access and test reports
#
# Steps: check GitLab -> ./install.sh -> qasight server init -> docker compose up ->
# backfill past pipelines -> cron every SYNC_EVERY_MIN minutes.
# Guide: docs/Processes & Guides/Closed Network Install.md
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
BUNDLE="$(pwd)"
CHECK_ONLY=0
case "${1:-}" in
  --check) CHECK_ONLY=1 ;;
  -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
esac

[ -f site.env ] || { echo "no site.env: cp site.env.example site.env and fill it in" >&2; exit 2; }
# shellcheck disable=SC1091
. ./site.env
# shellcheck disable=SC1091
. ./bundle.env
: "${GITLAB_URL:?set GITLAB_URL in site.env}" "${GITLAB_PROJECT:?set GITLAB_PROJECT in site.env}"
GITLAB_URL="${GITLAB_URL%/}"
SERVER_DIR="${SERVER_DIR:-/opt/qasight-server}"
SERVER_PORT="${SERVER_PORT:-8000}"
BACKFILL_RUNS="${BACKFILL_RUNS:-300}"
SYNC_EVERY_MIN="${SYNC_EVERY_MIN:-15}"

if [ -z "${TESTO_GITLAB_TOKEN:-}" ]; then
  read -r -s -p "GitLab token with read_api on $GITLAB_PROJECT: " TESTO_GITLAB_TOKEN
  echo
fi
[ -n "$TESTO_GITLAB_TOKEN" ] || { echo "a GitLab token is needed" >&2; exit 2; }
export TESTO_GITLAB_TOKEN

echo "== 1/6 checking GitLab"
PROJECT_ENC="$(printf '%s' "$GITLAB_PROJECT" | sed 's#/#%2F#g')"
API="$GITLAB_URL/api/v4/projects/$PROJECT_ENC"
CURL=(curl -fsS --max-time 30 -H "PRIVATE-TOKEN: $TESTO_GITLAB_TOKEN")
[ -n "${GITLAB_CA:-}" ] && CURL+=(--cacert "$GITLAB_CA")
PIPES="$("${CURL[@]}" "$API/pipelines?per_page=20&scope=finished")" || {
  echo "GitLab refused or can't be reached at $API (token scope read_api? project path? CA?)" >&2
  exit 1
}
IDS="$(printf '%s' "$PIPES" | grep -o '"id":[0-9]*' | cut -d: -f2 | head -20)"
[ -n "$IDS" ] || { echo "no finished pipelines in $GITLAB_PROJECT yet" >&2; exit 1; }
WITH_REPORTS=0
for id in $IDS; do
  count="$("${CURL[@]}" "$API/pipelines/$id/test_report_summary" 2>/dev/null \
    | grep -o '"count":[0-9]*' | head -1 | cut -d: -f2 || true)"
  [ "${count:-0}" -gt 0 ] && WITH_REPORTS=$((WITH_REPORTS + 1))
done
echo "   $WITH_REPORTS of $(echo "$IDS" | wc -w) recent pipelines have test reports"
if [ "$WITH_REPORTS" = "0" ]; then
  echo "   No job declares artifacts:reports:junit, so there is nothing to read yet. Use the" >&2
  echo "   one-include path instead (gitlab/qasight-every-job.gitlab-ci.yml, see the guide)." >&2
  [ "$CHECK_ONLY" = "1" ] && exit 1
fi
[ "$CHECK_ONLY" = "1" ] && { echo "== GitLab is ready for the same-day path"; exit 0; }

echo "== 2/6 installing the bundle"
INSTALL=(./install.sh)
[ -n "${REGISTRY:-}" ] && INSTALL+=(--registry "$REGISTRY")
"${INSTALL[@]}"
QASIGHT="$BUNDLE/venv/bin/qasight"

echo "== 3/6 team server in $SERVER_DIR"
if [ -f "$SERVER_DIR/.env" ]; then
  echo "   $SERVER_DIR/.env exists: keeping it"
else
  "$QASIGHT" server init --dir "$SERVER_DIR" >/dev/null
fi
cd "$SERVER_DIR"
if ! grep -q '^# quickstart' .env; then
  {
    echo "# quickstart"
    echo "TESTO_SERVER_TAG=$VERSION"
    echo "TESTO_SERVER_PORT=$SERVER_PORT"
    echo "TESTO_NO_UPDATE_CHECK=1"
    if [ -n "${REGISTRY:-}" ]; then
      echo "TESTO_REGISTRY=${REGISTRY%/}"
      echo "TESTO_POSTGRES_IMAGE=${REGISTRY%/}/postgres:16-alpine"
    fi
    if [ -n "${AI_BASE_URL:-}" ]; then
      echo "TESTO_AI_PROVIDER=openai_compatible"
      echo "TESTO_AI_BASE_URL=$AI_BASE_URL"
      echo "TESTO_AI_MODEL=${AI_MODEL:-}"
      echo "TESTO_AI_TIMEOUT_S=${AI_TIMEOUT_S:-120}"
    fi
  } >>.env
fi
chmod 600 .env

echo "== 4/6 starting it"
docker compose up -d
for _ in $(seq 1 60); do
  curl -fsS --max-time 3 "http://localhost:$SERVER_PORT/api/v1/health/live" >/dev/null 2>&1 && break
  sleep 3
done
curl -fsS --max-time 3 "http://localhost:$SERVER_PORT/api/v1/health/live" >/dev/null || {
  echo "the server didn't answer on port $SERVER_PORT: docker compose logs server" >&2
  exit 1
}

echo "== 5/6 reading past pipelines (up to $BACKFILL_RUNS)"
umask 077
printf '%s\n' "$TESTO_GITLAB_TOKEN" >gitlab-token
[ -n "${GITLAB_CA:-}" ] && cp "$GITLAB_CA" gitlab-ca.pem
cat >gitlab-sync.sh <<EOF
#!/usr/bin/env bash
# Record new GitLab pipelines (written by quickstart.sh). Usage: ./gitlab-sync.sh [RUNS]
set -euo pipefail
cd "\$(dirname "\${BASH_SOURCE[0]}")"
exec 9>.gitlab-sync.lock
flock -n 9 || exit 0
TESTO_GITLAB_TOKEN="\$(cat gitlab-token)"
export TESTO_GITLAB_TOKEN
EXTRA=()
if [ -f gitlab-ca.pem ]; then
  docker compose cp gitlab-ca.pem server:/tmp/gitlab-ca.pem
  EXTRA+=(-e REQUESTS_CA_BUNDLE=/tmp/gitlab-ca.pem)
fi
BRANCH=()
[ -n "${GITLAB_BRANCH:-}" ] && BRANCH=(--branch "${GITLAB_BRANCH:-}")
docker compose exec -T -e TESTO_GITLAB_TOKEN "\${EXTRA[@]+"\${EXTRA[@]}"}" server \\
  qasight ingest --from-gitlab --gitlab-url "$GITLAB_URL" --project "$GITLAB_PROJECT" \\
  --runs "\${1:-50}" "\${BRANCH[@]+"\${BRANCH[@]}"}"
EOF
chmod 700 gitlab-sync.sh
./gitlab-sync.sh "$BACKFILL_RUNS" || echo "   the first pass reported a problem (above); the cron retries it"

echo "== 6/6 every $SYNC_EVERY_MIN minutes"
LINE="*/$SYNC_EVERY_MIN * * * * $SERVER_DIR/gitlab-sync.sh 50 >>$SERVER_DIR/gitlab-sync.log 2>&1"
if command -v crontab >/dev/null; then
  { crontab -l 2>/dev/null | grep -v 'gitlab-sync.sh' || true; echo "$LINE"; } | crontab -
  echo "   cron: $LINE"
else
  echo "   no crontab here: add this line to a systemd timer or another scheduler:"
  echo "   $LINE"
fi

echo
echo "== done: http://$(hostname -f 2>/dev/null || hostname):$SERVER_PORT"
echo "Sign in with TESTO_API_TOKEN from $SERVER_DIR/.env. CI uploads (the one-include path) use it too."
