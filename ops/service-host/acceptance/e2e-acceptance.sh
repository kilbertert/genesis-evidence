#!/bin/bash
#
# Post-migration end-to-end acceptance.
#
# Runs the portal checks against the PUBLIC entry. The review workbench binds
# loopback — its exposure was withdrawn on observed traffic — so its checks run
# over the private channel instead of the public one; that is what the host tool
# below is for. Shelling out through it also covers the checks that need
# privileged access to the host (the review bearer, the migrated report data)
# rather than assuming those inputs already exist. That is what makes the
# workflow reproducible: previously the operator had to assemble intermediate
# files by hand, and the committed scripts did not compose.
#
# Usage: e2e-acceptance.sh <host-address>
#
# Exits nonzero on any failure and prints a summary. Every temporary file is
# created under a single private directory removed on exit.

set -uo pipefail

HOST_ADDR=${1:?host address required}
PORTAL="http://${HOST_ADDR}:10007"
STATE=${E2E_STATE:-/tmp/e2e-state.json}

WORK=$(mktemp -d)
chmod 700 "$WORK"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

pass=0; fail=0
ok() { printf 'PASS  %s\n' "$1"; pass=$((pass+1)); }
no() { printf 'FAIL  %s — %s\n' "$1" "$2" >&2; fail=$((fail+1)); }

# Run a command on the service host, returning its stdout.
on_host() { dev-host exec 36 --allow-service-exec -- "$1"; }

# GET a review-workbench path over the private channel, as root on the host.
# The listener is loopback-only, so this is the only way to reach it — and the
# right one: the point of the withdrawal is that the public entry no longer
# serves this surface at all.
#
# The bearer is read and used on the host and never crosses to this side, so it
# cannot be interpolated into a remote command — an operator-supplied key
# containing a quote must not become shell syntax on the service host. The
# response is written under a private directory removed on exit, so an
# interrupted run leaves no protected review data in the shared /tmp.
REVIEW_ENV=/opt/genesis-evidence/var/review.env
review_get() {
  on_host "KEY=\$(grep '^GENESIS_EVIDENCE_REVIEW_API_KEY=' $REVIEW_ENV | cut -d= -f2-); \
    [ -z \"\$KEY\" ] && exit 3; \
    d=\$(mktemp -d) && trap 'rm -rf \"\$d\"' EXIT; \
    curl -s -o \"\$d/out\" --max-time 25 -H \"Authorization: Bearer \$KEY\" \
      'http://127.0.0.1:10006$1'; cat \"\$d/out\"" 2>/dev/null
}

# Status code only, for the negative checks.
review_code() {
  on_host "curl -s -o /dev/null -w '%{http_code}' --max-time 25 \
    'http://127.0.0.1:10006$1'"
}

echo "== 1. the portal is reachable at the public entry; both internal services are not =="
c=$(curl -s -o "$WORK/portal.html" -w '%{http_code}' --max-time 20 "$PORTAL/")
[ "$c" = 200 ] && ok "portal HTTP 200" || no "portal reachable" "got $c"
grep -q "健康流" "$WORK/portal.html" && ok "portal serves the product UI" || no "portal UI" "no product title"

echo "== 2. boundaries hold (negative) =="
for path in /api/auth/me /api/auth/reports; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$PORTAL$path")
  [ "$c" = 401 ] && ok "portal $path refused without session" || no "portal $path" "got $c"
done

echo "== 3. internal listeners are NOT publicly exposed =="
# 10005 is the evidence API; 10006 is the review workbench, whose public
# binding was withdrawn on observed traffic. Both must refuse from outside.
for port in 10005 10006; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://${HOST_ADDR}:${port}/health")
  [ "$c" = "000" ] && ok "internal listener unreachable from outside (:${port})" \
    || no "listener exposure (:${port})" "got $c (should be refused)"
done

echo "== 3b. the review workbench answers over the private channel =="
c=$(review_code "/api/review/papers")
[ "$c" = 401 ] && ok "review API refused without bearer (private channel)" || no "review API (no bearer)" "got $c"

# The workbench page itself, so a missing UI cannot pass as a clean run.
page=$(on_host "curl -s --max-time 20 'http://127.0.0.1:10006/'" 2>/dev/null)
printf '%s' "$page" | grep -q "论文证据" && ok "review workbench page served (private channel)" \
  || no "review UI" "no workbench title over the private channel"

papers=$(review_get "/api/review/papers")
n=$(printf '%s' "$papers" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('papers',[])))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 100 ] && ok "migrated papers visible over the private channel ($n)" || no "migrated papers" "got ${n:-none}"

conds=$(review_get "/api/review/conditions")
n=$(printf '%s' "$conds" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('conditions',[])))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 10 ] && ok "conditions catalogue served ($n)" || no "conditions" "got ${n:-none}"

echo "== 4. metric catalogue (portal -> evidence service bridge) =="
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
