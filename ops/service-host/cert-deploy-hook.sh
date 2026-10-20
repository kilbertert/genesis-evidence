#!/bin/bash
#
# Per-lineage certbot deploy hook for the patient portal's entry.
#
# Installed as `/etc/letsencrypt/renewal-hooks/deploy/`? **No.** It is registered
# on the one lineage it serves:
#
#   certbot certonly --webroot -w /www/wwwroot/health.ranlei.work \
#       -d health.ranlei.work --key-type ecdsa \
#       --deploy-hook /opt/genesis-evidence/ops/cert-deploy-hook.sh
#
# which records it in `/etc/letsencrypt/renewal/health.ranlei.work.conf` and
# nowhere else. A hook in the global `renewal-hooks/deploy/` directory runs for
# **every** certificate renewed on this host — that is a scope decision nobody
# made for us: renewal of a company site would copy our files and reload the
# server on our account.
#
# What it does: copy the renewed certificate into the host's per-site directory,
# verify nginx accepts the configuration, then reload. Certbot passes
# `RENEWED_LINEAGE` and `RENEWED_DOMAINS`; the lineage is asserted against the
# domain this hook is for, so a mis-registration fails loudly instead of
# silently overwriting another site's certificate.
#
# **It must be tested by running it, not by `certbot renew --dry-run`.**
# Measured on certbot 2.9.0: `--dry-run` skips deploy hooks entirely, so a dry
# run turning green proves the ACME exchange works and says nothing about this
# script. The verification that counts is executing it once with
# `RENEWED_LINEAGE` set to the live lineage (see `docs/deployment.md`).

set -euo pipefail

DOMAIN=health.ranlei.work
SITE_CERT_DIR=/www/server/panel/vhost/cert/$DOMAIN
ACME_LIVE=/etc/letsencrypt/live/$DOMAIN

lineage=${RENEWED_LINEAGE:-}
if [ -z "$lineage" ]; then
  echo "cert-deploy-hook: RENEWED_LINEAGE is not set — this is a renewal hook, not a script to run by hand" >&2
  exit 1
fi
if [ "$lineage" != "$ACME_LIVE" ]; then
  echo "cert-deploy-hook: invoked for '$lineage', but this hook owns '$ACME_LIVE'" >&2
  exit 1
fi

# nginx's worker cannot read `/etc/letsencrypt/live` (root:root 700), so the
# certificate is copied into the panel's per-site directory — the same copy
# convention the neighbouring TLS site uses. Skipping the copy "because the
# service starts fine" relies on the master parsing configuration as root, which
# is an implicit assumption rather than a contract.
install -m 600 "$ACME_LIVE/fullchain.pem" "$SITE_CERT_DIR/fullchain.pem"
install -m 600 "$ACME_LIVE/privkey.pem"   "$SITE_CERT_DIR/privkey.pem"

# Test before reload. A configuration that fails here is left alone: the old
# certificate stays in place and nginx keeps serving, which is strictly better
# than reloading into a broken state.
if ! nginx -t; then
  echo "cert-deploy-hook: nginx -t failed; certificate copied but NOT reloaded" >&2
  exit 1
fi

# The panel-managed nginx may not be the unit that owns the ports, and its LSB
# unit can be failed while nginx serves traffic — so the signal goes to the
# running master through its pid file rather than through systemctl.
nginx -s reload
