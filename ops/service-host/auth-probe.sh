#!/bin/bash
#
# Assert the evidence API's key boundary, from the host itself.
#
# Run as the service identity. The key is read from the environment file.
#
# The third case is expected to be a 4xx *business* rejection, not 200: a fresh
# deployment has an empty database, so nothing is published to match against.
# What it proves is that authentication passed and the request reached the
# domain logic — which is why "not 401" is the assertion, not "200".
#
# A malformed request body would fail validation before the handler's key check
# and return 422 whatever the key is, so the body below is deliberately
# well-formed for v2.
#
# Exit status is nonzero if any case does not match. It prints the observed code
# for each case so a failure can be read directly.

set -uo pipefail

ENV_FILE=${ENV_FILE:-/opt/genesis-evidence/var/portal.env}
ENDPOINT=${ENDPOINT:-http://127.0.0.1:10005/api/evidence/matches}
TIMEOUT=${TIMEOUT:-10}

status_of() { # status_of <extra-curl-arg...> -- reads the body on stdin
  curl -s -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" \
    -X POST "$ENDPOINT" -H 'Content-Type: application/json' "$@" -d "$BODY"
}

BODY='{"schema_version":"2","observations":[{"observation_id":"probe-1",'
BODY+='"confirmation_status":"confirmed","metric_code":"fasting_glucose",'
BODY+='"value":7.2,"unit":"mmol/L","reference_low":3.9,"reference_high":6.1,'
BODY+='"evidence_text":"空腹血糖 7.2 mmol/L 参考范围 3.9-6.1",'
BODY+='"source_file_index":1,"source_page":1}]}'

if [ ! -r "$ENV_FILE" ]; then
  printf 'auth-probe: cannot read %s\n' "$ENV_FILE" >&2
  exit 65
fi
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

if [ -z "${GENESIS_EVIDENCE_API_KEY:-}" ]; then
  printf 'auth-probe: %s does not define GENESIS_EVIDENCE_API_KEY\n' "$ENV_FILE" >&2
  exit 65
fi

failures=0
check() { # check <label> <expected-description> <actual> <predicate>
  local label=$1 expected=$2 actual=$3
  if "$4" "$actual"; then
    printf 'ok    %-14s %s\n' "$label" "$actual"
  else
    printf 'FAIL  %-14s got %s, expected %s\n' "$label" "$actual" "$expected" >&2
    failures=$((failures + 1))
  fi
}

is_401() { [ "$1" = "401" ]; }

# Correct key: must NOT be a 401. Any 2xx/4xx from the domain logic is fine.
is_not_401() { [ "$1" != "401" ]; }

check "no key"      "401" "$(status_of)"                                          is_401
check "wrong key"   "401" "$(status_of -H 'X-Genesis-Evidence-Key: wrong-key-value-000000000000')" is_401
check "correct key" "not 401" "$(status_of -H "X-Genesis-Evidence-Key: $GENESIS_EVIDENCE_API_KEY")" is_not_401

if [ "$failures" -ne 0 ]; then
  printf '\nauth-probe: %d case(s) failed — the key boundary is NOT verified\n' "$failures" >&2
  exit 1
fi
printf '\nauth-probe: key boundary verified\n'
