#!/bin/bash
#
# Post-migration end-to-end acceptance.
#
# Runs entirely against the PUBLIC entry (http://<host>:<port>), as a real user
# would. Nothing is probed on loopback. Read-only with respect to migrated data:
# it never creates accounts or reports, and cleans up anything it does create.
#
# Usage: e2e-acceptance.sh <host-address>
#
# Exits nonzero on any failure and prints a summary.

set -uo pipefail

HOST_ADDR=${1:?host address required}
PORTAL="http://${HOST_ADDR}:10007"
REVIEW="http://${HOST_ADDR}:10006"

pass=0; fail=0
ok() { printf 'PASS  %s\n' "$1"; pass=$((pass+1)); }
no() { printf 'FAIL  %s — %s\n' "$1" "$2" >&2; fail=$((fail+1)); }

echo "== 1. both services reachable at the public entry =="
c=$(curl -s -o /tmp/e2e-p.html -w '%{http_code}' --max-time 20 "$PORTAL/")
[ "$c" = 200 ] && ok "portal HTTP 200" || no "portal reachable" "got $c"
grep -q "健康流" /tmp/e2e-p.html && ok "portal serves the product UI" || no "portal UI" "no product title"
c=$(curl -s -o /tmp/e2e-r.html -w '%{http_code}' --max-time 20 "$REVIEW/")
[ "$c" = 200 ] && ok "review HTTP 200" || no "review reachable" "got $c"
grep -q "论文证据" /tmp/e2e-r.html && ok "review serves the workbench UI" || no "review UI" "no workbench title"

echo "== 2. boundaries hold (negative) =="
for path in /api/auth/me /api/auth/reports; do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$PORTAL$path")
  [ "$c" = 401 ] && ok "portal $path refused without session" || no "portal $path" "got $c"
done
c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$REVIEW/api/review/papers")
[ "$c" = 401 ] && ok "review API refused without bearer" || no "review API (no bearer)" "got $c"
c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H 'Authorization: Bearer wrong-0000000000000000' "$REVIEW/api/review/papers")
[ "$c" = 401 ] && ok "review API refused with wrong bearer" || no "review API (wrong bearer)" "got $c"

echo "== 3. the evidence API is NOT publicly exposed =="
c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://${HOST_ADDR}:10005/health")
[ "$c" = "000" ] && ok "evidence API unreachable from outside (:10005)" || no "evidence API exposure" "got $c (should be refused)"

echo "== 4. migrated evidence content is served (via review API, authenticated) =="
KEY=$(cat /tmp/e2e-review-key)
papers=$(curl -s --max-time 25 -H "Authorization: Bearer $KEY" "$REVIEW/api/review/papers")
n=$(printf '%s' "$papers" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('papers',[])))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 100 ] && ok "migrated papers visible through the entry ($n)" || no "migrated papers" "got ${n:-none} (expected ~424)"

conds=$(curl -s --max-time 25 -H "Authorization: Bearer $KEY" "$REVIEW/api/review/conditions")
n=$(printf '%s' "$conds" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('conditions',[])))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 10 ] && ok "conditions catalogue served ($n)" || no "conditions" "got ${n:-none}"

echo "== 5. metric catalogue (portal -> evidence service bridge) =="
cat=$(curl -s --max-time 20 "$PORTAL/api/health/metric-catalog")
n=$(printf '%s' "$cat" | python3 -c "import json,sys;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 20 ] && ok "metric catalogue bridged ($n codes)" || no "metric catalogue" "got ${n:-none}"

echo "== 6. a migrated user's report and its file are retrievable =="
# Uses a short-lived session minted for an existing migrated account, then
# removes it. No user data is created or modified.
REPORT_ID=$(cat /tmp/e2e-report-id); FILE_IDX=$(cat /tmp/e2e-file-idx)
# Pass the cookie as a literal name=value, not as a file: `curl -b <file>`
# expects Netscape cookie-jar format and silently sends nothing for a plain
# name=value file, which reads as an auth failure rather than a test bug.
COOKIE=$(cat /tmp/e2e-cookie)
c=$(curl -s -o /tmp/e2e-page.bin -w '%{http_code}' --max-time 25 \
      -b "$COOKIE" "$PORTAL/api/health/report/$REPORT_ID/files/$FILE_IDX/pages/1")
if [ "$c" = 200 ]; then
  sz=$(stat -c%s /tmp/e2e-page.bin)
  [ "$sz" -gt 1000 ] && ok "migrated report page served ($sz bytes)" || no "report page size" "$sz bytes"
else
  no "migrated report page" "got $c"
fi
c=$(curl -s -o /tmp/e2e-report.json -w '%{http_code}' --max-time 25 \
      -b "$COOKIE" "$PORTAL/api/health/report/$REPORT_ID")
if [ "$c" = 200 ]; then
  st=$(python3 -c "import json;print(json.load(open('/tmp/e2e-report.json')).get('status',''))" 2>/dev/null)
  ok "migrated report metadata served (status=$st)"
else
  no "migrated report metadata" "got $c"
fi

echo
printf 'SUMMARY pass=%d fail=%d\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
echo "END-TO-END ACCEPTANCE PASSED"
