#!/bin/bash
#
# Assert the patient-facing evidence chain: a confirmed abnormal metric yields a
# published knowledge card, not an empty match.
#
# This runs ON the service host because the evidence API is deliberately not
# exposed publicly — it is an internal edge that only health-flow calls. The
# public acceptance suite cannot reach it by design, which is why this assertion
# lives here rather than being folded into that suite.
#
# Exits nonzero if the finding or its card is missing.

set -uo pipefail

ENV_FILE=${ENV_FILE:-/opt/genesis-evidence/var/portal.env}
ENDPOINT=${ENDPOINT:-http://127.0.0.1:10005/api/evidence/matches}

if [ ! -r "$ENV_FILE" ]; then
  printf 'match-probe: cannot read %s\n' "$ENV_FILE" >&2; exit 65
fi
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
if [ -z "${GENESIS_EVIDENCE_API_KEY:-}" ]; then
  printf 'match-probe: %s defines no GENESIS_EVIDENCE_API_KEY\n' "$ENV_FILE" >&2; exit 65
fi

BODY='{"schema_version":"3","observations":[{"observation_id":"probe-1",'
BODY+='"confirmation_status":"confirmed","metric_code":"fasting_glucose",'
BODY+='"value":7.2,"unit":"mmol/L","reference_low":3.9,"reference_high":6.1,'
BODY+='"evidence_text":"空腹血糖 7.2 mmol/L 参考范围 3.9-6.1",'
BODY+='"source_file_index":1,"source_page":1}]}'

out=$(mktemp)
trap 'rm -f "$out"' EXIT
code=$(curl -s -o "$out" -w '%{http_code}' --max-time 20 -X POST "$ENDPOINT" \
  -H 'Content-Type: application/json' -H "X-Genesis-Evidence-Key: $GENESIS_EVIDENCE_API_KEY" \
  -d "$BODY")

fail=0
[ "$code" = "200" ] || { printf 'FAIL  match endpoint returned %s\n' "$code" >&2; fail=1; }

python3 - "$out" <<'PY' || fail=1
import json, sys
data = json.load(open(sys.argv[1]))
findings = data.get("findings") or []
if not findings:
    print("FAIL  no finding returned:", data.get("message") or data.get("unmatched"), file=sys.stderr)
    raise SystemExit(1)
f = findings[0]
if f.get("condition_code") != "COND_PREDIABETES":
    print(f"FAIL  unexpected condition: {f.get('condition_code')}", file=sys.stderr)
    raise SystemExit(1)
items = f.get("evidence_items") or []
cards = [i for i in items if i.get("card")]
if not cards:
    print("FAIL  finding carries no published knowledge card", file=sys.stderr)
    raise SystemExit(1)
card = cards[0]["card"]
if not card.get("id"):
    print("FAIL  published card has no identity", file=sys.stderr)
    raise SystemExit(1)
if card.get("status") != "published":
    print(f"FAIL  card status is {card.get('status')}, not published", file=sys.stderr)
    raise SystemExit(1)
if not (card.get("patient_visible_body") or "").strip():
    print("FAIL  published card has no patient-visible body", file=sys.stderr)
    raise SystemExit(1)
# Traceability: the card must name the claim and paper it came from.
sources = card.get("sources") or []
if not sources:
    print("FAIL  published card carries no claim/paper traceability", file=sys.stderr)
    raise SystemExit(1)
src = sources[0]
if not (src.get("paper_title") or src.get("doi")):
    print("FAIL  card source names no paper", file=sys.stderr)
    raise SystemExit(1)
print(f"ok    finding {f['condition_code']} carries a published card")
print(f"ok    card {card['id']} grade={card.get('grade')} status={card.get('status')}")
print(f"ok    traceable to {len(sources)} source(s), e.g. doi={src.get('doi')}")
print(f"ok    evidence strength {f.get('evidence_strength')}")
PY

if [ "$fail" -ne 0 ]; then
  printf '\nmatch-probe: patient-facing evidence chain NOT verified\n' >&2
  exit 1
fi
printf '\nmatch-probe: patient-facing evidence chain verified\n'
