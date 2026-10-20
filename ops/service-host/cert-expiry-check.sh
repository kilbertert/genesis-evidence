#!/bin/bash
#
# Fail when a certificate we are responsible for is close to expiring, and keep a
# ledger of every certificate on the host either way.
#
#   cert-expiry-check.sh [--self-check] [CERT_DIR ...]
#
# Each CERT_DIR is a directory holding `fullchain.pem` (the shape the host's web
# server already uses: one directory per site). With no CERT_DIR, the script
# sweeps /www/server/panel/vhost/cert — every site on the host.
#
# **Two classes, because responsibility is not uniform.** This host is shared.
# It carries the company's sites, whose certificates we may read but must not
# change: their renewal is not ours to fix, and a unit that goes permanently red
# because someone else's certificate lapsed is a unit whose red stops being read.
#
#   owned    — listed in OWNED_DIRS below. These decide the exit status.
#   observed — everything else found in the sweep. Reported, never judged.
#
# The ledger is the point of `observed`: our gate not judging a certificate is
# not the same as nobody knowing. When one of them lapses, it is visible in the
# output (and in the ticket that follows it up), not silently absent.
#
# Output is grouped by domain, one line each: domain / not-after / days left /
# class. An `observed` line inside the window is marked `EXPIRING*` and the `*`
# is explained in the summary, so a reader cannot mistake "recorded" for
# "cleared".
# Exit status:
#   0  every *owned* certificate has more than THRESHOLD_DAYS left
#   1  at least one *owned* certificate has THRESHOLD_DAYS or fewer  ← the alarm
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
# second. It also feeds an *observed* site that is inside the window beside a
# healthy owned one, and asserts the run passes while still reporting it — that
# is the only way to prove the two classes stay separate. **A gate that has never
# been fed a failing sample is a bell nobody has rung**; this is the runnable
# proof that it rings, and a second sample proving it stays quiet.

set -uo pipefail

THRESHOLD_DAYS=${THRESHOLD_DAYS:-21}
# Where a bare run looks for certificates. One directory per site.
CERT_ROOT=${CERT_ROOT:-/www/server/panel/vhost/cert}

# The certificates this deployment answers for. One directory per site, matching
# the host's layout. A retired domain's certificate is deliberately **absent**:
# it has no consumer, and counting it would make this gate fail forever for a
# site nobody visits. If a domain is added here, its directory must hold
# `fullchain.pem`; the script asserts that rather than assuming it.
#
# Anything found in the sweep and *not* in this list is observed only. Do not add
# a company site here to make its lapse visible: it is visible in the ledger
# already, and judging it would make this unit depend on an owner we are not.
OWNED_DIRS=(
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
  # Prefer a DNS SAN: a Cloudflare Origin certificate's subject CN is
  # "CloudFlare Origin Certificate", not the domain, so the subject alone names
  # the wrong thing for exactly the certificates this host has the most of.
  # Fall back to the subject CN only when there is no DNS SAN at all.
  local name
  name=$(openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null \
    | tr ', ' '\n\n' | sed -n 's/^DNS://p' | head -1)
  if [ -z "$name" ]; then
    name=$(openssl x509 -in "$1" -noout -subject -nameopt RFC2253 2>/dev/null \
      | sed -n 's/^subject=.*CN=\([^,]*\).*/\1/p' | head -1)
  fi
  printf '%s' "$name"
}

# Is this directory one we answer for? Compares resolved paths, so a caller
# passing a trailing slash or a symlinked root lands in the same class as the
# bare run would.
is_owned() { # is_owned <dir>
  local want here owned
  want=$(readlink -f -- "$1" 2>/dev/null || printf '%s' "$1")
  for owned in "${OWNED_DIRS[@]}"; do
    here=$(readlink -f -- "$owned" 2>/dev/null || printf '%s' "$owned")
    [ "$here" = "$want" ] && return 0
  done
  return 1
}

check_dirs() {
  local failures=0 readable=0 observed_expiring=0 dir pem days domain class
  for dir in "$@"; do
    pem="$dir/fullchain.pem"
    if is_owned "$dir"; then class=owned; else class=observed; fi
    if [ ! -r "$pem" ]; then
      printf 'UNREADABLE  %-40s %s — no readable fullchain.pem\n' \
        "$(basename "$dir")" "[$class]" >&2
      # An unreadable *observed* site is recorded, not judged: its certificate
      # belongs to an owner who can act on it, and failing here would make this
      # unit's status a function of someone else's directory permissions.
      if [ "$class" = owned ]; then failures=$((failures + 1)); fi
      continue
    fi
    if ! days=$(days_left "$pem"); then
      printf 'UNREADABLE  %-40s %s — certificate has no parseable expiry\n' \
        "$(basename "$dir")" "[$class]" >&2
      if [ "$class" = owned ]; then failures=$((failures + 1)); fi
      continue
    fi
    readable=$((readable + 1))
    domain=$(domain_of "$pem")
    [ -n "$domain" ] || domain=$dir
    local end
    end=$(openssl x509 -in "$pem" -noout -enddate | cut -d= -f2-)
    if [ "$days" -le "$THRESHOLD_DAYS" ]; then
      if [ "$class" = owned ]; then
        printf 'EXPIRING    %-40s %s  %s day(s) left (threshold %s) [owned]\n' \
          "$domain" "$end" "$days" "$THRESHOLD_DAYS" >&2
        failures=$((failures + 1))
      else
        printf 'EXPIRING*   %-40s %s  %s day(s) left (threshold %s) [observed]\n' \
          "$domain" "$end" "$days" "$THRESHOLD_DAYS"
        observed_expiring=$((observed_expiring + 1))
      fi
    else
      printf 'ok          %-40s %s  %s day(s) left [%s]\n' \
        "$domain" "$end" "$days" "$class"
    fi
  done
  # The `*` is only meaningful if something explains it, and only on the run that
  # produced it: a permanent legend would be read as boilerplate.
  if [ "$observed_expiring" -gt 0 ]; then
    printf '\n* EXPIRING* marks a certificate this deployment does NOT answer for:\n' >&2
    printf '  it is recorded so its lapse is visible, and it does not affect the exit\n' >&2
    printf '  status. %s certificate(s) in that class this run.\n' "$observed_expiring" >&2
  fi
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

  # The first samples are *owned* by construction: they are asking whether an
  # expiring certificate in the class we answer for rings the bell. Without this
  # they would be classified observed and correctly say nothing.
  local saved_owned=("${OWNED_DIRS[@]}")
  OWNED_DIRS=("$work/bad" "$work/good" "$work/does-not-exist")
  rc_bad=0
  check_dirs "$work/bad" >/dev/null 2>&1 || rc_bad=$?
  rc_good=0
  check_dirs "$work/good" >/dev/null 2>&1 || rc_good=$?
  OWNED_DIRS=("${saved_owned[@]}")

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
  OWNED_DIRS=("$work/does-not-exist")
  check_dirs "$work/does-not-exist" >/dev/null 2>&1 || rc_missing=$?
  OWNED_DIRS=("${saved_owned[@]}")
  if [ "$rc_missing" -ne 0 ]; then
    printf 'ok    self-check: unreadable path fails closed (rc=%s)\n' "$rc_missing"
  else
    printf 'FAIL  self-check: unreadable path passed — a blind check reads as healthy\n' >&2
    failed=1
  fi

  # ── The two classes stay separate ─────────────────────────────────────────
  # Build a sweep shape: an *observed* site inside the window next to an *owned*
  # one that is healthy. The run must exit 0 — we do not answer for the other
  # owner's certificate — and it must still say so, because a lapse we record and
  # a lapse we judge are both worth knowing about.
  #
  # The owned directory is named after the real one under a temp root, and
  # OWNED_DIRS is rewritten for this check only. Re-implementing the classification
  # inside the self-check would test a copy of the rule rather than the rule.
  local saved_owned=("${OWNED_DIRS[@]}")
  mkdir -p "$work/root/site-of-ours" "$work/root/site-of-theirs"
  cp "$work/good/fullchain.pem" "$work/root/site-of-ours/fullchain.pem"
  cp "$work/bad/fullchain.pem"  "$work/root/site-of-theirs/fullchain.pem"
  OWNED_DIRS=("$work/root/site-of-ours")
  local mixed_out rc_mixed=0
  mixed_out=$(check_dirs "$work/root/site-of-ours" "$work/root/site-of-theirs" 2>&1) || rc_mixed=$?
  OWNED_DIRS=("${saved_owned[@]}")

  if [ "$rc_mixed" -eq 0 ]; then
    printf 'ok    self-check: an observed lapse does not judge the run (rc=%s)\n' "$rc_mixed"
  else
    printf 'FAIL  self-check: an observed lapse failed the run (rc=%s) — our unit is judged by a certificate we do not own\n' "$rc_mixed" >&2
    failed=1
  fi
  if printf '%s' "$mixed_out" | grep -q 'EXPIRING\*'; then
    printf 'ok    self-check: the observed lapse is still reported\n'
  else
    printf 'FAIL  self-check: the observed lapse vanished from the ledger — recorded is not the same as ignored\n' >&2
    failed=1
  fi

  # ...and the mirror: a lapse in the *owned* class must judge the run, or the
  # previous assertion would also pass if classification did nothing at all.
  OWNED_DIRS=("$work/root/site-of-theirs")
  local rc_owned=0
  check_dirs "$work/root/site-of-ours" "$work/root/site-of-theirs" >/dev/null 2>&1 || rc_owned=$?
  OWNED_DIRS=("${saved_owned[@]}")
  if [ "$rc_owned" -ne 0 ]; then
    printf 'ok    self-check: an owned lapse judges the run (rc=%s)\n' "$rc_owned"
  else
    printf 'FAIL  self-check: an owned lapse passed — classification is inert\n' >&2
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
  # Sweep every site on the host: the owned ones decide the exit status, the rest
  # are reported. A missing root is the fail-closed case above, not a quiet pass.
  shopt -s nullglob
  all_dirs=("$CERT_ROOT"/*)
  shopt -u nullglob
  check_dirs "${all_dirs[@]}"
fi
