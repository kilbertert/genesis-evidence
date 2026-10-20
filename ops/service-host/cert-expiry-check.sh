#!/bin/bash
#
# Fail when a certificate we are responsible for is close to expiring.
#
#   cert-expiry-check.sh [--self-check] [CERT_DIR ...]
#
# Each CERT_DIR is a directory holding `fullchain.pem` (the shape the host's web
# server already uses: one directory per site). With no CERT_DIR, the script
# reads the deployment's own list below — the certificates this deployment is
# answerable for, which is not "every certificate on the host".
#
# Output is grouped by domain, one line each: domain / not-after / days left.
# Exit status:
#   0  every certificate has more than THRESHOLD_DAYS left
#   1  at least one has THRESHOLD_DAYS or fewer  ← the alarm
#   2  nothing could be read (missing directory, unreadable or undated
#      certificate). A check that cannot see a certificate must not report
#      "healthy": that is how a silent failure becomes invisible.
#
# **Read-only.** It opens and parses certificates. It never writes a key, never
# rewrites a chain, never reloads anything. It walks directories that also hold
# the company's own sites' certificates, and that is deliberate: reading them is
# how we notice the host's renewal pipeline is broken, without touching them.
#
# **It can only be seen, not notified.** Until this deployment has an alerting
# channel, the signal is the unit entering `failed` and appearing in
# `systemctl --failed`. That is the only signal on this host that gets looked at;
# do not read its existence as a notification path.
#
# Why 21 days and not 14: Let's Encrypt renews at 30 days before expiry, twice a
# day. 21 days means the renewal window has been open for ~9 days and has failed
# ~18 times — enough to rule out a transient, with time left for a human. A
# 14-day threshold leaves a week and turns "renewal broke" into an outage.
#
# `--self-check` builds one certificate that expires inside the window and one
# that does not, and asserts the check fails on the first and passes on the
# second. **A gate that has never been fed a failing sample is a bell nobody has
# rung**; this is the runnable proof that it rings. It needs `openssl` and a
# writable temporary directory, nothing else.

set -uo pipefail

THRESHOLD_DAYS=${THRESHOLD_DAYS:-21}

# The certificates this deployment answers for. One directory per site, matching
# the host's layout. A retired domain's certificate is deliberately **absent**:
# it has no consumer, and counting it would make this gate fail forever for a
# site nobody visits. If a domain is added here, its directory must hold
# `fullchain.pem`; the script asserts that rather than assuming it.
DEFAULT_CERT_DIRS=(
  /www/server/panel/vhost/cert/health.ranlei.work
)

# Days from now until the certificate expires. Prints an integer, or nothing.
days_left() { # days_left <fullchain.pem>
  local pem=$1 end
  end=$(openssl x509 -in "$pem" -noout -enddate 2>/dev/null | cut -d= -f2-)
  [ -n "$end" ] || return 1
  local end_s now_s
  end_s=$(date -d "$end" +%s 2>/dev/null) || return 1
  now_s=$(date +%s)
  # Integer days, floored toward zero: "expires in 20.9 days" must not print 21
  # and then pass a 21-day gate for another 21 hours.
  echo $(( (end_s - now_s) / 86400 ))
}

domain_of() { # domain_of <fullchain.pem>
  openssl x509 -in "$1" -noout -subject -nameopt RFC2253 2>/dev/null \
    | sed -n 's/^subject=.*CN=\([^,]*\).*/\1/p' | head -1
}

check_dirs() {
  local failures=0 readable=0 dir pem days domain
  for dir in "$@"; do
    pem="$dir/fullchain.pem"
    if [ ! -r "$pem" ]; then
      printf 'UNREADABLE  %s — no readable fullchain.pem\n' "$dir" >&2
      failures=$((failures + 1))
      continue
    fi
    if ! days=$(days_left "$pem"); then
      printf 'UNREADABLE  %s — certificate has no parseable expiry\n' "$pem" >&2
      failures=$((failures + 1))
      continue
    fi
    readable=$((readable + 1))
    domain=$(domain_of "$pem")
    [ -n "$domain" ] || domain=$dir
    if [ "$days" -le "$THRESHOLD_DAYS" ]; then
      printf 'EXPIRING    %-40s %s  %s day(s) left (threshold %s)\n' \
        "$domain" "$(openssl x509 -in "$pem" -noout -enddate | cut -d= -f2-)" "$days" "$THRESHOLD_DAYS" >&2
      failures=$((failures + 1))
    else
      printf 'ok          %-40s %s  %s day(s) left\n' \
        "$domain" "$(openssl x509 -in "$pem" -noout -enddate | cut -d= -f2-)" "$days"
    fi
  done
  # A run that read nothing is a failure, not a quiet pass: a wrong path or an
  # unreadable directory would otherwise look exactly like "all good".
  if [ "$readable" -eq 0 ]; then
    printf 'cert-expiry-check: no certificate could be read\n' >&2
    return 1
  fi
  [ "$failures" -eq 0 ]
}

self_check() {
  local work cert_bad cert_good rc_bad rc_good
  work=$(mktemp -d)
  trap 'rm -rf "$work"' RETURN
  mkdir -p "$work/bad" "$work/good"
  # `-days 10` is inside a 21-day window; `-days 90` is not.
  openssl req -x509 -newkey rsa:2048 -nodes -days 10 \
    -subj "/CN=expiring.invalid" \
    -keyout "$work/bad/key.pem" -out "$work/bad/fullchain.pem" >/dev/null 2>&1
  openssl req -x509 -newkey rsa:2048 -nodes -days 90 \
    -subj "/CN=fresh.invalid" \
    -keyout "$work/good/key.pem" -out "$work/good/fullchain.pem" >/dev/null 2>&1
  if [ ! -s "$work/bad/fullchain.pem" ] || [ ! -s "$work/good/fullchain.pem" ]; then
    printf 'self-check: could not generate sample certificates\n' >&2
    return 1
  fi

  rc_bad=0
  check_dirs "$work/bad" >/dev/null 2>&1 || rc_bad=$?
  rc_good=0
  check_dirs "$work/good" >/dev/null 2>&1 || rc_good=$?

  local failed=0
  if [ "$rc_bad" -ne 0 ]; then
    printf 'ok    self-check: expiring sample is rejected (rc=%s)\n' "$rc_bad"
  else
    printf 'FAIL  self-check: expiring sample was accepted — the gate does not ring\n' >&2
    failed=1
  fi
  if [ "$rc_good" -eq 0 ]; then
    printf 'ok    self-check: fresh sample passes\n'
  else
    printf 'FAIL  self-check: fresh sample was rejected (rc=%s) — the gate is noise\n' "$rc_good" >&2
    failed=1
  fi
  # And a path that does not exist must fail closed rather than pass quietly.
  local rc_missing=0
  check_dirs "$work/does-not-exist" >/dev/null 2>&1 || rc_missing=$?
  if [ "$rc_missing" -ne 0 ]; then
    printf 'ok    self-check: unreadable path fails closed (rc=%s)\n' "$rc_missing"
  else
    printf 'FAIL  self-check: unreadable path passed — a blind check reads as healthy\n' >&2
    failed=1
  fi
  return "$failed"
}

if [ "${1:-}" = "--self-check" ]; then
  self_check
  exit $?
fi

if [ "$#" -gt 0 ]; then
  check_dirs "$@"
else
  check_dirs "${DEFAULT_CERT_DIRS[@]}"
fi
