#!/bin/bash
#
# Post-migration end-to-end acceptance.
#
# Runs against the PUBLIC entry and, for the checks that need privileged access
# to the host (the review API and the migrated report data), shells out through
# the project's host tool rather than assuming those inputs already exist. That
# is what makes the workflow reproducible: previously the operator had to
# assemble intermediate files by hand, and the committed scripts did not compose.
#
# Usage: e2e-acceptance.sh <host-address>
#
# Exits nonzero on any failure and prints a summary. Every temporary file is
# created under a single private directory removed on exit.

set -uo pipefail

HOST_ADDR=${1:?host address required}
PORTAL="http://${HOST_ADDR}:10007"
REVIEW="http://${HOST_ADDR}:10006"
STATE=${E2E_STATE:-/tmp/e2e-state.json}

WORK=$(mktemp -d)
chmod 700 "$WORK"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

pass=0; fail=0
ok() { printf 'PASS  %s\n' "$1"; pass=$((pass+1)); }
no() { printf 'FAIL  %s — %s\n' "$1" "$2" >&2; fail=$((fail+1)); }

# Run a command on the service host as root, returning its stdout.
on_host() { dev-host exec 36 --allow-service-exec -- "$1"; }

echo "== 1. both services reachable at the public entry =="
c=$(curl -s -o "$WORK/portal.html" -w '%{http_code}' --max-time 20 "$PORTAL/")
[ "$c" = 200 ] && ok "portal HTTP 200" || no "portal reachable" "got $c"
grep -q "健康流" "$WORK/portal.html" && ok "portal serves the product UI" || no "portal UI" "no product title"
c=$(curl -s -o "$WORK/review.html" -w '%{http_code}' --max-time 20 "$REVIEW/")
[ "$c" = 200 ] && ok "review HTTP 200" || no "review reachable" "got $c"
grep -q "论文证据" "$WORK/review.html" && ok "review serves the workbench UI" || no "review UI" "no workbench title"

echo "== 2. boundaries hold (negative) =="
for path in /api/auth/me /api/auth/reports; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$PORTAL$path")
  [ "$c" = 401 ] && ok "portal $path refused without session" || no "portal $path" "got $c"
done
c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$REVIEW/api/review/papers")
[ "$c" = 401 ] && ok "review API refused without bearer" || no "review API (no bearer)" "got $c"
c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
      -H 'Authorization: Bearer wrong-0000000000000000' "$REVIEW/api/review/papers")
[ "$c" = 401 ] && ok "review API refused with wrong bearer" || no "review API (wrong bearer)" "got $c"

echo "== 3. the evidence API is NOT publicly exposed =="
c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://${HOST_ADDR}:10005/health")
[ "$c" = "000" ] && ok "evidence API unreachable from outside (:10005)" || no "evidence API exposure" "got $c (should be refused)"

echo "== 4. migrated evidence content is served (via review API, authenticated) =="
# The review bearer lives on the host; read it there and never write it to disk.
KEY=$(on_host "grep '^GENESIS_EVIDENCE_REVIEW_API_KEY=' /opt/genesis-evidence/var/review.env | cut -d= -f2-")
if [ -z "$KEY" ]; then
  no "review bearer readable" "could not read from the host"
else
  papers=$(curl -s --max-time 25 -H "Authorization: Bearer $KEY" "$REVIEW/api/review/papers")
  n=$(printf '%s' "$papers" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('papers',[])))" 2>/dev/null || echo 0)
  [ "${n:-0}" -ge 100 ] && ok "migrated papers visible through the entry ($n)" || no "migrated papers" "got ${n:-none}"

  conds=$(curl -s --max-time 25 -H "Authorization: Bearer $KEY" "$REVIEW/api/review/conditions")
  n=$(printf '%s' "$conds" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('conditions',[])))" 2>/dev/null || echo 0)
  [ "${n:-0}" -ge 10 ] && ok "conditions catalogue served ($n)" || no "conditions" "got ${n:-none}"
fi

echo "== 5. metric catalogue (portal -> evidence service bridge) =="
cat=$(curl -s --max-time 20 "$PORTAL/api/health/metric-catalog")
n=$(printf '%s' "$cat" | python3 -c "import json,sys;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 20 ] && ok "metric catalogue bridged ($n codes)" || no "metric catalogue" "got ${n:-none}"

echo "== 6. an existing user's report and its page file are retrievable =="
if [ ! -f "$STATE" ]; then
  no "probe state present" "$STATE missing — run mint-probe-session.py first"
else
  COOKIE=$(python3 -c "import json;print(json.load(open('$STATE'))['cookie'])" 2>/dev/null || echo "")
  REPORT_ID=$(python3 -c "import json;print(json.load(open('$STATE'))['report_id'])" 2>/dev/null || echo "")
  FILE_IDX=$(python3 -c "import json;print(json.load(open('$STATE'))['file_index'])" 2>/dev/null || echo "")

  c=$(curl -s -o "$WORK/page.bin" -w '%{http_code}' --max-time 25 \
        -b "$COOKIE" "$PORTAL/api/health/report/$REPORT_ID/files/$FILE_IDX/pages/1")
  if [ "$c" = 200 ]; then
    sz=$(stat -c%s "$WORK/page.bin")
    [ "$sz" -gt 1000 ] && ok "report page served ($sz bytes)" || no "report page size" "$sz bytes"
  else
    no "report page retrievable" "got $c"
  fi

  c=$(curl -s -o "$WORK/report.json" -w '%{http_code}' --max-time 25 \
        -b "$COOKIE" "$PORTAL/api/health/report/$REPORT_ID")
  if [ "$c" != 200 ]; then
    no "report metadata served" "got $c"
  elif ! python3 -c "
import json
d = json.load(open('$WORK/report.json'))
status = str(d.get('status') or '').strip()
assert status, 'report has no status field'
assert str(d.get('id')) == '$REPORT_ID', 'reported id does not match the requested report'
" 2>"$WORK/parse.err"; then
    no "report metadata content valid" "$(head -c 120 "$WORK/parse.err" | tr '\n' ' ')"
  else
    st=$(python3 -c "import json;print(json.load(open('$WORK/report.json')).get('status'))")
    ok "report metadata valid (status=$st, id matches)"
  fi
fi

echo
printf 'SUMMARY pass=%d fail=%d\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
echo "END-TO-END ACCEPTANCE PASSED"
