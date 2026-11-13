#!/usr/bin/env bash
# `make cloud-smoke`: is the cloud demo up, private and safe? Runs on the Mac.
#   1. HTTPS with a valid certificate; http:// redirects; HSTS on pages and on /api
#   2. noindex header, robots.txt disallows everything, the synthetic-data banner is shown
#   3. internal ports (Postgres, Temporal, Langfuse, MLflow, API, Next.js) are closed
#   4. a clinician can log in; the cookies are Secure and HttpOnly
#   5. Silas Marmere is found by name; his timeline and patient page load
#   6. no OpenAI key on the server, no Langfuse database
#   7. surveillance is pinned to the data cut-off: no run after it, the latest run is on it
#   8. the demo guide is on, guest access is open, and both guest accounts log in
#   9. the copilot's 6 suggested questions are answered from the cache (no API key)
# --fresh (right after a deploy): also no login attempt or refresh token carried over.
# The clinician password is read from the Keychain into a variable, never printed.
set -euo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=lib.sh
source infra/azure/lib.sh

FRESH=0
[[ "${1:-}" == "--fresh" ]] && FRESH=1
BANNER="Synthetic data. Clinical decision support only, not diagnosis."
PATIENT_GIVEN=Silas PATIENT_FAMILY=Marmere LOGIN=clinician.center_a
need_az
resolve_host
URL="https://$VM_HOST"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
FAILED=0

check() {  # check <description> <command...>
  local what="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "    ok    $what"
  else
    echo "    FAIL  $what"
    FAILED=1
  fi
}
header() { grep -qi "^$2:" "$1"; }
header_has() { grep -i "^$2:" "$1" | grep -qi "$3"; }
port_closed() { ! nc -z -G 3 "$VM_HOST" "$1"; }
status_is() { [[ "$(curl -s -o /dev/null -w '%{http_code}' "$@")" == "${STATUS:-200}" ]]; }

step "Smoke test of $URL"
curl -fsS -D "$WORK/page.h" -o /dev/null "$URL/login" || true
curl -sS -D "$WORK/api.h" -o /dev/null "$URL/api/health" || true
curl -sS -D "$WORK/http.h" -o /dev/null "http://$VM_HOST/login" || true
check "HTTPS answers with a valid certificate" status_is "$URL/login"
check "http:// redirects to https://" header_has "$WORK/http.h" location "^location: https://"
check "HSTS on pages" header "$WORK/page.h" strict-transport-security
check "HSTS on /api" header "$WORK/api.h" strict-transport-security
check "X-Robots-Tag: noindex" header_has "$WORK/page.h" x-robots-tag noindex
curl -fsS "$URL/robots.txt" > "$WORK/robots.txt" || true
check "robots.txt disallows everything" grep -qx "Disallow: /" "$WORK/robots.txt"

for port in 3000 3001 5001 5432 7233 8000 8233; do
  check "port $port closed from the Internet" port_closed "$port"
done

ssh_vm "bash /opt/txlens/infra/azure/remote/facts.sh" > "$WORK/facts"
fact() { [[ "$(awk -v k="$1" '$1 == k {print $2}' "$WORK/facts")" == "$2" ]]; }
check "no OpenAI key in the server's .env" fact env_file_openai_key 0
check "no OpenAI key in the API container" fact api_openai_key 0
check "no OpenAI key in the worker container" fact worker_openai_key 0
check "no Langfuse database" fact langfuse_db 0
check "surveillance pinned: DATA_CUTOFF set" fact data_cutoff_set 1
check "surveillance pinned: the worker sees DATA_CUTOFF" fact worker_data_cutoff_set 1
check "no surveillance run after the data cut-off" fact runs_after_cutoff 0
check "latest surveillance run is as of the cut-off" fact latest_run_is_cutoff 1
check "both guest accounts exist and are active" fact guest_accounts 2
check "guest expiry in the database matches .env" fact guest_expiry_matches_env 1
check "copilot: all 6 suggested questions answered from the cache" fact copilot_suggestions_cached 6
if [[ "$FRESH" == 1 ]]; then
  check "no login attempts carried over" fact login_attempts 0
  check "no refresh tokens carried over" fact refresh_tokens 0
fi

PASSWORD=$(keychain_password clinician) || fail "no clinician password in the Keychain (make cloud-passwords)"
COOKIES="$WORK/cookies"
api() { curl -sS -b "$COOKIES" -c "$COOKIES" -H "X-Requested-With: smoke" "$@"; }
# The JSON body goes through stdin, so the password is not in the process list.
printf '{"username": "%s", "password": "%s"}' "$LOGIN" "$PASSWORD" \
  | api -D "$WORK/login.h" -o /dev/null -H "Content-Type: application/json" --data-binary @- \
    "$URL/api/auth/login" || true
unset PASSWORD
check "clinician login" grep -q "^HTTP/[0-9.]* 200" "$WORK/login.h"
check "session cookies are Secure" header_has "$WORK/login.h" set-cookie "; *secure"
check "session cookies are HttpOnly" header_has "$WORK/login.h" set-cookie "; *httponly"

api "$URL/api/patients?q=$PATIENT_FAMILY" > "$WORK/patients.json" || true
PATIENT_ID=$(python3 - "$WORK/patients.json" "$PATIENT_GIVEN" "$PATIENT_FAMILY" <<'PY' || true
import json, sys
items = json.load(open(sys.argv[1])).get("items", [])
match = [p for p in items if (p.get("given_name"), p.get("family_name")) == tuple(sys.argv[2:])]
print(match[0]["patient_id"] if match else "")
PY
)
check "$PATIENT_GIVEN $PATIENT_FAMILY found by name" test -n "$PATIENT_ID"
check "$PATIENT_GIVEN $PATIENT_FAMILY's timeline loads" status_is -b "$COOKIES" \
  "$URL/api/patients/${PATIENT_ID:-none}/timeline"
# The page renders the patient in the browser (from the timeline above); the server sends the
# app shell with the banner.
check "$PATIENT_GIVEN $PATIENT_FAMILY's page loads" status_is -b "$COOKIES" \
  "$URL/patients/${PATIENT_ID:-none}"
api -o "$WORK/patient.html" "$URL/patients/${PATIENT_ID:-none}" || true
check "synthetic-data banner on the page" grep -qF "$BANNER" "$WORK/patient.html"
api -o /dev/null -X POST "$URL/api/auth/logout" || true

# The login page's demo guide and the guest accounts (password from the Keychain).
curl -fsS "$URL/api/demo" > "$WORK/demo.json" || true
guide_open() {
  python3 -c 'import json, sys, datetime as d
info = json.load(open(sys.argv[1]))
until = d.datetime.fromisoformat(info["guest_until"])
sys.exit(0 if info["guide"] and until > d.datetime.now(d.UTC) else 1)' "$WORK/demo.json"
}
check "demo guide on, guest access open (until $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("guest_until"))' "$WORK/demo.json" 2>/dev/null))" guide_open
GUEST_PASSWORD=$(keychain_password guest) || fail "no guest password in the Keychain (make cloud-passwords)"
for guest in clinician researcher; do
  rm -f "$COOKIES"
  printf '{"username": "guest.%s", "password": "%s"}' "$guest" "$GUEST_PASSWORD" \
    | api -o "$WORK/guest.json" -H "Content-Type: application/json" --data-binary @- \
      "$URL/api/auth/login" || true
  check "guest.$guest logs in as $guest" grep -q "\"role\": *\"$guest\"" "$WORK/guest.json"
  api -o /dev/null -X POST "$URL/api/auth/logout" || true
done
unset GUEST_PASSWORD

if [[ "$FAILED" == 1 ]]; then
  fail "smoke test failed (see FAIL lines above)"
fi
echo "    all checks passed"
