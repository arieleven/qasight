#!/usr/bin/env bash
# Build qasight-upload (uploader/, Go) and upload to a real `qasight-api` from this
# checkout, so a change to POST /api/v1/ingest that breaks the Go uploader fails CI.
# Needs Go and this checkout installed with the api extra (pip install -e ".[api]").
# Usage: scripts/upload_binary_smoke.sh   (TESTO_API_PORT defaults to 8765)
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
port="${TESTO_API_PORT:-8765}"
base="http://127.0.0.1:$port"
work="$(mktemp -d)"
token="smoke-$RANDOM-$RANDOM"
bin="$work/qasight-upload"

(cd "$root/uploader" && go build -o "$bin" .)

mkdir -p "$work/server" "$work/reports/nested"
cat > "$work/reports/unit.xml" <<'XML'
<testsuite name="cart"><testcase classname="cart" name="adds item" time="0.2"/></testsuite>
XML
cat > "$work/reports/nested/e2e.xml" <<'XML'
<testsuite name="checkout"><testcase classname="checkout" name="pays" time="1.5">
<failure message="timeout">Timed out</failure></testcase></testsuite>
XML

cd "$work/server"   # the server keeps its history here
TESTO_API_TOKEN="$token" TESTO_API_PORT="$port" TESTO_NO_UPDATE_CHECK=1 \
  qasight-api > "$work/server.log" 2>&1 &
server_pid=$!
cleanup() {
  kill "$server_pid" 2>/dev/null || true
  wait "$server_pid" 2>/dev/null || true
  grep -v ' 20[01] ' "$work/server.log" | tail -n 20 || true
  rm -rf "$work"
}
trap cleanup EXIT
for _ in $(seq 60); do
  curl -fsS "$base/api/v1/health/live" >/dev/null 2>&1 && break
  sleep 1
done

cd "$work"
export TESTO_SERVER_URL="$base"

# A wrong token is refused (exit 3) and not retried.
set +e
"$bin" --token wrong --cycle smoke --retries 0 reports/unit.xml
code=$?
set -e
test "$code" -eq 3

export TESTO_API_TOKEN="$token"
"$bin" --cycle smoke reports/unit.xml   # every test passed: exit 0

set +e
"$bin" --cycle smoke --json --env browser=firefox --release 2.3 'reports/**/*.xml' > upload.json
code=$?
set -e
cat upload.json
test "$code" -eq 1   # a failed test: exit 1, like `qasight ingest`
python3 - upload.json <<'PY'
import json, sys
event = json.load(open(sys.argv[1]))
assert event["event"] == "ingest_uploaded", event
assert (event["tests"], event["failed"]) == (2, 1), event
assert event["run_id"], event
PY

# The server recorded both runs, with the metadata the uploader sent.
curl -fsS -H "Authorization: Bearer $token" "$base/api/v1/runs" | python3 -c '
import json, sys
items = json.load(sys.stdin)["items"]
assert len(items) == 2, items
assert {i["cycle"] for i in items} == {"smoke"}, items
newest = max(items, key=lambda i: i["created_at"])
assert (newest["environment"], newest["release"]) == ("browser=firefox", "2.3"), newest
'
echo "qasight-upload smoke passed"
