#!/bin/bash
# Negative/positive auth probe against the new evidence API.
BODY='{"schema_version":"2","observations":[{"observation_id":"probe-1","confirmation_status":"confirmed","metric_code":"LDL_C","value":3.4,"unit":"mmol/L","evidence_text":"probe","source_file_index":1,"source_page":1}]}'
echo -n "no key        -> "
curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 -X POST http://127.0.0.1:10005/api/evidence/matches \
  -H 'Content-Type: application/json' -d "$BODY"
echo -n "wrong key     -> "
curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 -X POST http://127.0.0.1:10005/api/evidence/matches \
  -H 'Content-Type: application/json' -H 'X-Genesis-Evidence-Key: wrong-key-value-000000000000' -d "$BODY"
echo -n "correct key   -> "
set -a; . /opt/genesis-evidence/var/portal.env; set +a
curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 -X POST http://127.0.0.1:10005/api/evidence/matches \
  -H 'Content-Type: application/json' -H "X-Genesis-Evidence-Key: $GENESIS_EVIDENCE_API_KEY" -d "$BODY"
