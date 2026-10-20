#!/bin/bash
#
# End-to-end acceptance — the one acceptance script for this deployment.
#
# Runs the portal checks against the **public entry**. The review workbench binds
# loopback — its exposure was withdrawn on observed traffic — so its checks run
# over the private channel instead of the public one; that is what the host tool
# below is for. Shelling out through it also covers the checks that need
# privileged access to the host (the review bearer, the migrated report data)
# rather than assuming those inputs already exist. That is what makes the
# workflow reproducible: previously the operator had to assemble intermediate
# files by hand, and the committed scripts did not compose.
#
# Usage: e2e-acceptance.sh <entry-base-url>
#
#   e2e-acceptance.sh                                  # https://health.ranlei.work
#   e2e-acceptance.sh https://health.ranlei.work       # explicit, same thing
#   e2e-acceptance.sh http://<host-address>:10007      # the pre-TLS entry, for a
#                                                      # rollback comparison
#
# The base URL is a parameter because the entry is a moving target by design:
# this suite had to keep working across the IP:port → named+TLS transition, and
# a second copy of it for the new entry is how two scripts start disagreeing
# about the same deployment. Certificate verification is on by default; `INSECURE=1`
# is a deliberate, visible opt-out for a certificate that is legitimately not
# publicly trusted, not a blanket `-k`.
#
# Exits nonzero on any failure and prints a summary. Every temporary file is
# created under a single private directory removed on exit.
#
# Prerequisite for section 6: a session minted by `mint-probe-session.py` (the
# service host's copy, in this directory). Run `cleanup-probe-session.py` after.

set -uo pipefail

HOST_ADDR=${HOST_ADDR:-36.156.159.175}
ENTRY=${1:-https://health.ranlei.work}
PORTAL=${ENTRY%/}
STATE=${E2E_STATE:-/tmp/e2e-state.json}
INSECURE=${INSECURE:-0}
TLS=()
[ "$INSECURE" = "1" ] && TLS=(-k)

WORK=$(mktemp -d)
chmod 700 "$WORK"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

pass=0; fail=0; skip=0
ok() { printf 'PASS  %s\n' "$1"; pass=$((pass+1)); }
no() { printf 'FAIL  %s — %s\n' "$1" "$2" >&2; fail=$((fail+1)); }
# A skipped case is printed, counted, and **not** silently treated as a pass. It
# exists for assertions that only make sense against one of the two entries this
# suite can be pointed at; the alternative — running them anyway — makes a
# rollback comparison fail for a reason that has nothing to do with the rollback.
skip() { printf 'SKIP  %s — %s\n' "$1" "$2"; skip=$((skip+1)); }

# The hostname the entry was reached by, or empty when it is an IP address.
# Everything that is a property of the *named* entry (its log file, its
# certificate) keys off this rather than off "which URL did I pass".
entry_host=${PORTAL#*://}
entry_host=${entry_host%%/*}
entry_host=${entry_host%%:*}

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
# response is streamed to stdout and never written to a file, so there is no
# host-side copy to be left behind by an interruption: a trap cannot clean up
# after SIGKILL, which is what sinks temp-file variants of this.
REVIEW_ENV=/opt/genesis-evidence/var/review.env
review_get() {
  on_host "KEY=\$(grep '^GENESIS_EVIDENCE_REVIEW_API_KEY=' $REVIEW_ENV | cut -d= -f2-); \
    [ -z \"\$KEY\" ] && exit 3; \
    curl -s --max-time 25 -H \"Authorization: Bearer \$KEY\" \
      'http://127.0.0.1:10006$1'" 2>/dev/null
}

# A request carrying a deliberately wrong bearer, for the negative check. Same
# streaming treatment; the wrong key is a literal, not a secret.
review_get_with_wrong_key() {
  on_host "curl -s -o /dev/null -w '%{http_code}' --max-time 25 \
    -H 'Authorization: Bearer wrong-0000000000000000' 'http://127.0.0.1:10006$1'"
}

# Status code only, for the negative checks.
review_code() {
  on_host "curl -s -o /dev/null -w '%{http_code}' --max-time 25 \
    'http://127.0.0.1:10006$1'"
}

echo "== 1. the portal is reachable at the public entry; both internal services are not =="
c=$(curl -s "${TLS[@]}" -o "$WORK/portal.html" -w '%{http_code}' --max-time 20 "$PORTAL/")
[ "$c" = 200 ] && ok "portal HTTP 200" || no "portal reachable" "got $c"
grep -q "健康流" "$WORK/portal.html" && ok "portal serves the product UI" || no "portal UI" "no product title"

# An unrelated Host over the same protocol must not land on this application.
# Checking content rather than a status code is deliberate: the host's own
# catch-all answers 443 with `444`, and a default site elsewhere can answer 200.
other=$(curl -s "${TLS[@]}" --max-time 20 --resolve "entry-probe.invalid:443:${HOST_ADDR}" \
  "https://entry-probe.invalid/" 2>/dev/null)
if printf '%s' "$other" | grep -q "健康流"; then
  no "unrelated Host does not reach the portal" "the portal answered for entry-probe.invalid"
else
  ok "unrelated Host does not reach the portal"
fi

echo "== 2. boundaries hold (negative) =="
for path in /api/auth/me /api/auth/reports; do
  c=$(curl -s "${TLS[@]}" -o /dev/null -w '%{http_code}' --max-time 20 "$PORTAL$path")
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
c=$(review_get_with_wrong_key "/api/review/papers")
[ "$c" = 401 ] && ok "review API refused with wrong bearer (private channel)" || no "review API (wrong bearer)" "got $c"

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
cat=$(curl -s "${TLS[@]}" --max-time 20 "$PORTAL/api/health/metric-catalog")
n=$(printf '%s' "$cat" | python3 -c "import json,sys;print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 20 ] && ok "metric catalogue bridged ($n codes)" || no "metric catalogue" "got ${n:-none}"

echo "== 5. the entry's access log exists and holds no ticket =="
# The mall's entry page arrives as `/?ticket=…`, and that ticket is a credential
# for 120 seconds. The log format uses `$uri` instead of `$request_uri` so the
# query string is never written; this asserts the property on the file the entry
# actually writes, which is the only place it can be observed.
#
# **This is an assertion about the named entry, not about the deployment.** Run
# against the pre-TLS `http://<host>:10007` entry — which has no hostname and
# therefore no such log — it would fail for the reason the rollback comparison
# is least able to afford: the new entry is not installed. So it is skipped
# there, out loud, and the suite's exit status does not depend on it.
LOG=/www/wwwlogs/health.ranlei.work.log
if [ "$entry_host" != "health.ranlei.work" ]; then
  skip "entry access log" "entry is '$entry_host'; this assertion only applies to health.ranlei.work"
elif on_host "test -r $LOG" >/dev/null 2>&1; then
  hits=$(on_host "grep -c 'ticket=' $LOG || true")
  case "$hits" in
    ""|0) ok "access log has no ticket= (read through the private channel)" ;;
    *)    no "access log has no ticket=" "$hits line(s) contain a ticket" ;;
  esac
else
  no "access log readable" "$LOG missing or unreadable on the host"
fi

echo "== 6. an existing user's report and its page file are retrievable =="
if [ ! -f "$STATE" ]; then
  no "probe state present" "$STATE missing — run mint-probe-session.py first"
else
  COOKIE=$(python3 -c "import json;print(json.load(open('$STATE'))['cookie'])" 2>/dev/null || echo "")
  REPORT_ID=$(python3 -c "import json;print(json.load(open('$STATE'))['report_id'])" 2>/dev/null || echo "")
  FILE_IDX=$(python3 -c "import json;print(json.load(open('$STATE'))['file_index'])" 2>/dev/null || echo "")

  c=$(curl -s "${TLS[@]}" -o "$WORK/page.bin" -w '%{http_code}' --max-time 25 \
        -b "$COOKIE" "$PORTAL/api/health/report/$REPORT_ID/files/$FILE_IDX/pages/1")
  if [ "$c" = 200 ]; then
    sz=$(stat -c%s "$WORK/page.bin")
    [ "$sz" -gt 1000 ] && ok "report page served ($sz bytes)" || no "report page size" "$sz bytes"
  else
    no "report page retrievable" "got $c"
  fi

  c=$(curl -s "${TLS[@]}" -o "$WORK/report.json" -w '%{http_code}' --max-time 25 \
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
printf 'SUMMARY pass=%d fail=%d skip=%d\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ] || exit 1
echo "END-TO-END ACCEPTANCE PASSED"
