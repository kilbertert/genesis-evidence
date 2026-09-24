#!/bin/bash
#
# Product acceptance for the migrated deployment, run against the PUBLIC entry.
#
# Everything here goes through the public hostname — resolved to the service
# host's address via --resolve — so it exercises the same path a real user takes:
# entry, TLS, proxy, service. Nothing is probed on loopback or on the host.
#
# Exits nonzero if any case fails, and prints a machine-readable summary.
#
# Usage:
#   acceptance.sh <host-address> <review-bearer-key>
#
# The reviewer key is passed in rather than read from a file so this script
# carries no credential.
#
# ---------------------------------------------------------------------------
# NOT RUNNABLE IN THE CURRENT DEPLOYMENT. It targets the retired
# `*.ranlei.work` hostnames over HTTPS, and the review workbench it probes no
# longer has a public entry (that exposure was withdrawn on observed traffic).
# Use `e2e-acceptance.sh` instead. This file is kept as the form to restore
# once a company subdomain and certificate exist — and when that happens the
# review checks still belong on the private channel, because the workbench does
# not become public just because a domain exists.
# ---------------------------------------------------------------------------

set -uo pipefail

HOST_ADDR=${1:?host address required}
REVIEW_KEY=${2:?review bearer key required}
# By default the suite verifies the certificate chain, so an invalid or
# mismatched certificate fails instead of passing. Set INSECURE=1 only when the
# certificate is legitimately not publicly trusted — a Cloudflare Origin
# certificate is trusted by Cloudflare, not by browsers, so reaching the origin
# directly requires it. Running this suite from the public internet through
# Cloudflare would verify the chain and INSECURE=1 would not be needed.
#
# This is a deliberate, visible opt-out rather than a blanket -k, so an
# accidental certificate problem is not silently tolerated.
INSECURE=${INSECURE:-0}
TLS=()
[ "$INSECURE" = "1" ] && TLS=(-k)

PORTAL=https://genesis-evidence.ranlei.work
REVIEW=https://genesis-evidence-review.ranlei.work
RESOLVE_PORTAL="--resolve genesis-evidence.ranlei.work:443:${HOST_ADDR}"
RESOLVE_REVIEW="--resolve genesis-evidence-review.ranlei.work:443:${HOST_ADDR}"

COOKIE=$(mktemp); TMPPDF=$(mktemp --suffix=.pdf)
trap 'rm -f "$COOKIE" "$TMPPDF"' EXIT
printf '%%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%%%EOF\n' > "$TMPPDF"

pass=0; fail=0
ok()   { printf 'PASS  %s\n' "$1"; pass=$((pass+1)); }
no()   { printf 'FAIL  %s — %s\n' "$1" "$2"; fail=$((fail+1)); }
code() { curl -s "${TLS[@]}" -o /dev/null -w '%{http_code}' --max-time 20 "$@"; }

EMAIL="acceptance-$$@example.invalid"
PASSWORD="acceptance-password-$$"

echo "== A. entry reachable and serving the real applications =="
for pair in "${PORTAL}|健康流" "${REVIEW}|审核"; do
  url=${pair%%|*}; want=${pair##*|}
  case "$url" in *review*) R="$RESOLVE_REVIEW";; *) R="$RESOLVE_PORTAL";; esac
  body=$(curl -s "${TLS[@]}" --max-time 20 $R "$url/" 2>/dev/null)
  if printf '%s' "$body" | grep -q "$want"; then ok "entry serves $url"; else no "entry serves $url" "title mismatch"; fi
done

echo "== B. account session boundary =="
c=$(code $RESOLVE_PORTAL -b "" "$PORTAL/api/auth/me")
[ "$c" = 401 ] && ok "protected route refused without session" || no "protected route refused without session" "got $c"
c=$(code $RESOLVE_PORTAL -b "" -X POST "$PORTAL/api/health/report/upload" -F "file=@$TMPPDF;type=application/pdf")
[ "$c" = 401 ] && ok "upload refused without session" || no "upload refused without session" "got $c"

c=$(code $RESOLVE_PORTAL -c "$COOKIE" -X POST "$PORTAL/api/auth/register" -H 'Content-Type: application/json' \
     -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
[ "$c" = 201 ] || [ "$c" = 200 ] && ok "registration creates a session" || no "registration creates a session" "got $c"
c=$(code $RESOLVE_PORTAL -b "$COOKIE" "$PORTAL/api/auth/me")
[ "$c" = 200 ] && ok "session grants access to protected route" || no "session grants access" "got $c"

echo "== C. public metric catalogue (the bridge target is reachable) =="
cat=$(curl -s "${TLS[@]}" --max-time 20 $RESOLVE_PORTAL "$PORTAL/api/health/metric-catalog" 2>/dev/null)
n=$(printf '%s' "$cat" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 1 ] && ok "metric catalog reachable ($n codes)" || no "metric catalog reachable" "got ${n:-none}"

echo "== D. reviewer bearer boundary (including the negative case) =="
c=$(code $RESOLVE_REVIEW "$REVIEW/api/review/papers")
[ "$c" = 401 ] && ok "review API refused without bearer" || no "review API refused without bearer" "got $c"
c=$(code $RESOLVE_REVIEW -H 'Authorization: Bearer wrong-key-0000000000000000' "$REVIEW/api/review/papers")
[ "$c" = 401 ] && ok "review API refused with wrong bearer" || no "review API refused with wrong bearer" "got $c"
c=$(code $RESOLVE_REVIEW -H "Authorization: Bearer $REVIEW_KEY" "$REVIEW/api/review/papers")
[ "$c" = 200 ] && ok "review API accepts the real bearer" || no "review API accepts the real bearer" "got $c"

echo "== E. report upload path (durable queue) =="
body=$(mktemp)
c=$(curl -s "${TLS[@]}" -o "$body" -w '%{http_code}' --max-time 30 $RESOLVE_PORTAL -b "$COOKIE" \
     -X POST "$PORTAL/api/health/report/upload" -F "file=@$TMPPDF;type=application/pdf")
[ "$c" = 202 ] && ok "upload returns 202" || no "upload returns 202" "got $c"
st=$(python3 -c "import json;print(json.load(open('$body')).get('status',''))" 2>/dev/null || echo "")
[ "$st" = "processing" ] && ok "upload status is processing" || no "upload status is processing" "got '$st'"

# Durability: the work is queued, not parsed inline. The worker is disabled, so
# a job that exists and is still queued proves persistence.
job=$(python3 -c "import json;print(json.load(open('$body')).get('extraction_job',{}).get('status',''))" 2>/dev/null || echo "")
[ "$job" = "queued" ] && ok "extraction job persisted as queued" || no "extraction job persisted as queued" "got '$job'"
rm -f "$body"

echo "== F. review queue is readable through the entry =="
# NOTE: this reports the paper queue, which is served regardless of whether any
# knowledge card references those papers. It therefore does NOT prove the
# patient-facing evidence chain, and passing it while every card were withdrawn
# would be a false negative. The chain itself is asserted by the loopback
# companion (ops/match-probe.sh) on the host, because the evidence API is
# deliberately not publicly exposed. Kept here as an entry-level smoke check.
papers=$(curl -s "${TLS[@]}" --max-time 25 $RESOLVE_REVIEW -H "Authorization: Bearer $REVIEW_KEY" \
         "$REVIEW/api/review/papers" 2>/dev/null)
n=$(printf '%s' "$papers" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('papers',[])))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 1 ] && ok "published papers readable through entry ($n)" \
  || no "published papers readable through entry" "got ${n:-none}"

echo "== G. conditions catalogue is served (fixture for the match path) =="
conds=$(curl -s "${TLS[@]}" --max-time 25 $RESOLVE_REVIEW -H "Authorization: Bearer $REVIEW_KEY" \
        "$REVIEW/api/review/conditions" 2>/dev/null)
n=$(printf '%s' "$conds" | python3 -c "import json,sys;d=json.load(sys.stdin);print(len(d) if isinstance(d,list) else len(d.get('conditions',[])))" 2>/dev/null || echo 0)
[ "${n:-0}" -ge 1 ] && ok "conditions catalogue served ($n)" || no "conditions catalogue served" "got ${n:-none}"

echo
printf 'SUMMARY pass=%d fail=%d\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
echo "ACCEPTANCE PASSED"
