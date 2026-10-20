"""The certificate-expiry gate must ring, and the entry configuration must hold
the properties the ticket made binding.

Both are files that no other test reads: a shell script that runs on the service
host, and an nginx configuration that is deployed there. Driving them here is
what turns "it looks right" into a check that fails when the property is lost.

The script's own `--self-check` is the interesting half — it builds a sample
that expires inside the window and asserts the gate rejects it. That mode exists
so this test does not have to re-implement the judgement; what this file adds is
that the self-check is *run at all*, on every change.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
ENTRY_CONF = ROOT / "ops/service-host/nginx/health.ranlei.work.conf"
EXPIRY_CHECK = ROOT / "ops/service-host/cert-expiry-check.sh"


def _entry_config() -> str:
    return ENTRY_CONF.read_text(encoding="utf-8")


def _server_blocks(config: str) -> list[str]:
    """Split the file into top-level `server { … }` blocks by brace depth.

    Deliberately not a full parser: the properties asserted below are textual
    (which directives appear in which block), and a real parser would add a
    dependency to answer a question about our own four directives.
    """
    blocks: list[str] = []
    depth = 0
    start = None
    for index, line in enumerate(config.splitlines()):
        stripped = line.strip()
        if depth == 0 and re.match(r"^server\s*\{", stripped):
            start = index
        depth += line.count("{") - line.count("}")
        if start is not None and depth == 0:
            blocks.append("\n".join(config.splitlines()[start : index + 1]))
            start = None
    return blocks


def _directives(text: str) -> str:
    """The file with comment lines removed.

    Several of the properties below are "this configuration must not do X while
    the comment may *name* X as the rejected option". Asserting on the raw text
    would make an explanatory comment fail the check it explains, which teaches
    the next reader to delete the explanation.
    """
    kept = [
        line
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(kept)


def test_entry_config_has_no_review_workbench_block() -> None:
    """The workbench's exposure was withdrawn on observed traffic (#... 2026-09-28).

    A `server` block naming it would reinstate that exposure as a side effect of
    an entry change, which is the one thing this file must never do.
    """

    config = _entry_config()
    assert "genesis-evidence-review" not in config
    assert "10006" not in config
    # And the only backend it proxies to is the portal.
    assert "proxy_pass http://127.0.0.1:10007;" in config
    assert len(re.findall(r"proxy_pass\s", config)) == 1


def test_entry_config_overwrites_the_proxy_chain() -> None:
    """Overwrite, not append.

    The application trusts proxy headers from a loopback peer, so an appended
    value leaves the client's own forged header at the head of the chain.
    """

    config = _directives(_entry_config())
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert "proxy_add_x_forwarded_for" not in config
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in config


def test_entry_config_logs_the_uri_and_not_the_request() -> None:
    """`/?ticket=…` is a credential; `$uri` drops it.

    Asserted on the format definition rather than on a rendered log line,
    because the point is which variable is chosen — a test that parsed output
    would pass a format that still embedded `$args` in a second field.
    """

    config = _entry_config()
    assert re.search(r"log_format\s+portal\b", config), "no dedicated format"
    fmt = config[config.index("log_format portal") :]
    fmt = fmt[: fmt.index(";")]
    assert '"uri":"$uri"' in fmt
    assert "$request_uri" not in fmt
    assert "$request\"" not in fmt
    assert "$args" not in fmt
    # Declared, not inherited: the host's global default is `access_log off`.
    assert re.search(r"access_log\s+/www/wwwlogs/health\.ranlei\.work\.log\s+portal;", config)


def test_entry_config_hsts_is_scoped_to_this_hostname() -> None:
    config = _entry_config()
    hsts = re.search(r'add_header Strict-Transport-Security "([^"]+)"', config)
    assert hsts is not None, "no HSTS header"
    value = hsts.group(1)
    assert "max-age=31536000" in value
    assert "includeSubDomains" not in value
    assert "preload" not in value


def test_entry_config_does_not_claim_the_default_server() -> None:
    """`default_server` belongs to the catch-all that sorts before this file.

    If this file claimed it, an unrelated Host reaching 443 would land on the
    patient portal — the failure this host has already had once.
    """

    config = _directives(_entry_config())
    assert "default_server" not in config
    for block in _server_blocks(config):
        assert "health.ranlei.work" in block or "acme-challenge" in block


def test_entry_config_serves_acme_on_80_and_redirects_the_rest() -> None:
    config = _entry_config()
    blocks = _server_blocks(config)
    port_80 = next(block for block in blocks if re.search(r"listen\s+80;", block))
    assert "/.well-known/acme-challenge/" in port_80
    # `$uri`, not `$request_uri`: the latter copies `?ticket=…` into the Location
    # header of a plaintext response. Pinned as an equality so a well-meaning
    # "preserve the query string" edit fails here rather than in a log.
    assert "return 301 https://$host$uri;" in port_80
    # The comment above the directive names `$request_uri` as the rejected
    # option, so the "must not contain" half is judged on directives only.
    assert "$request_uri" not in _directives(port_80)
    # No whole-site `root`: only the challenge prefix maps to disk.
    assert len(re.findall(r"\broot\s+", port_80)) == 1


@pytest.mark.parametrize("name", ["cert-expiry-check.sh"])
def test_cert_expiry_gate_rings_on_an_expiring_sample(name: str) -> None:
    """The gate is proven by feeding it a certificate inside the window.

    `--self-check` builds one that expires in 10 days and one in 90, and asserts
    the first is rejected and the second passes. A gate never shown to fail is a
    bell nobody has rung, so this runs it rather than reading it.
    """

    script = ROOT / "ops/service-host" / name
    result = subprocess.run(
        ["bash", str(script), "--self-check"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "expiring sample is rejected" in result.stdout
    assert "fresh sample passes" in result.stdout
    assert "unreadable path fails closed" in result.stdout
    assert "FAIL" not in result.stdout


def test_cert_expiry_gate_honours_its_declared_paths() -> None:
    """It judges what this deployment answers for, and sweeps the rest.

    Read from the declaration rather than from a copy of the list, so adding a
    site does not require editing two places. The sweep root is asserted too:
    the ledger is only a ledger if it actually looks at every site on the host.
    """

    script = EXPIRY_CHECK.read_text(encoding="utf-8")
    owned_block = script[script.index("OWNED_DIRS=(") :]
    owned_block = owned_block[: owned_block.index(")")]
    assert "health.ranlei.work" in owned_block
    # The retired genesis-evidence domain's certificate has no consumer; judging
    # it would make the gate fail forever for a site nobody visits.
    assert "genesis-evidence.ranlei.work" not in owned_block
    # Sweeping the whole host is what turns "we did not judge it" into "we saw
    # it". If this were narrowed to the owned list, a company certificate's lapse
    # would become invisible rather than merely unjudged.
    assert "CERT_ROOT=${CERT_ROOT:-/www/server/panel/vhost/cert}" in script
    # The bare run unions the sweep with the owned list. Without the union, an
    # owned certificate that has vanished from the root produces no row at all,
    # and the run passes on the strength of other people's certificates.
    assert 'check_dirs "${all_dirs[@]}" "${OWNED_DIRS[@]}"' in script


def test_cert_expiry_gate_separates_owned_from_observed() -> None:
    """A lapse we do not own is reported, and does not fail the unit.

    The host is shared: `api.qumall.qushiyun.com` lapsed to 10 days while this
    was written, and its renewal is not ours to fix. A unit that goes red for it
    is a unit whose red gets ignored — so the classification is the property, not
    an implementation detail. Asserted through the script's own samples rather
    than by re-reading a list, because the self-check runs the shipped code.
    """

    result = subprocess.run(
        ["bash", str(EXPIRY_CHECK), "--self-check"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Both directions, or classification could be inert and still pass one.
    assert "an observed lapse does not judge the run" in result.stdout
    assert "an owned lapse judges the run" in result.stdout
    assert "the observed lapse is still reported" in result.stdout
    assert "FAIL" not in result.stdout + result.stderr


def test_cert_expiry_gate_names_the_domain_not_the_issuer() -> None:
    """A Cloudflare Origin certificate's CN is the product name, not the site.

    Measured on the host: the subject reads `CN=CloudFlare Origin Certificate`
    for a `*.ranlei.work` certificate. A ledger keyed on that tells an operator
    nothing about which site is about to break, so the DNS SAN is what names a
    row.
    """

    script = EXPIRY_CHECK.read_text(encoding="utf-8")
    body = script[script.index("domain_of()") :]
    body = body[: body.index("\n}")]
    assert "subjectAltName" in body, "the ledger names the issuer instead of the site"
    assert body.index("subjectAltName") < body.index("subject -nameopt RFC2253")


def test_cert_hook_is_registered_per_lineage_not_globally() -> None:
    """A global deploy hook runs for every certificate on the host.

    This host renews other projects' certificates; a hook in
    `renewal-hooks/deploy/` would copy our files and reload the server on their
    renewal. The hook must be bound to its own lineage and must refuse a foreign
    one.
    """

    hook = (ROOT / "ops/service-host/cert-deploy-hook.sh").read_text(encoding="utf-8")
    assert "--deploy-hook" in hook, "the registration form is documented in the hook"
    assert "renewal-hooks" in hook  # the rejected placement is named
    assert '"$lineage" != "$ACME_LIVE"' in hook, "no lineage assertion"
    # A dry run does not execute deploy hooks (certbot 2.9.0, measured); the
    # hook must say so, or the next reader will "verify" it with one.
    assert "dry-run" in hook and "skips deploy hooks" in hook


def test_cert_hook_tests_the_config_before_reloading() -> None:
    hook = (ROOT / "ops/service-host/cert-deploy-hook.sh").read_text(encoding="utf-8")
    test_at = hook.index("nginx -t")
    reload_at = hook.index("nginx -s reload")
    assert test_at < reload_at, "reload is reachable without a config test"
    assert "exit 1" in hook[test_at:reload_at]


def test_logrotate_covers_only_our_two_files() -> None:
    """The host's log directory is shared; a glob would rotate someone else's."""

    conf = _directives(
        (ROOT / "ops/service-host/logrotate/health.ranlei.work").read_text(encoding="utf-8")
    )
    listing = conf[conf.index("/www/wwwlogs/") :]
    listing = listing[: listing.index("{")]
    assert "health.ranlei.work.log" in listing
    assert "health.ranlei.work.error.log" in listing
    assert "*" not in listing
    assert "copytruncate" not in conf
    assert "USR1" in conf


def test_cert_deploy_hook_is_executable() -> None:
    """certbot runs `--deploy-hook` as an **executable**, not through a shell.

    A 644 hook is the quietest possible failure: renewal succeeds, the copy and
    the reload never happen, and the portal goes on serving an expired
    certificate until someone notices the browser warning. The mode is asserted
    from git's index rather than the working tree, because that is what a deploy
    of this commit carries — an editor re-save can flip the disk copy back and
    still leave the committed mode intact.
    """

    result = subprocess.run(
        ["git", "ls-files", "-s", "ops/service-host/cert-deploy-hook.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.startswith("100755 "), (
        "cert-deploy-hook.sh is not executable in the index: "
        f"{result.stdout.split()[0] if result.stdout else '<absent>'}"
    )


def test_cert_expiry_gate_self_check_covers_the_escape_hatch() -> None:
    """`OWNED_DIRS_OVERRIDE` must not be usable outside `--self-check`.

    Set alone it would let whoever controls the environment choose which
    certificates this unit is judged by — that is the alarm, disabled by
    accident or on purpose. Asserted by running it, since the guard is a shell
    branch: without the marker it must exit 2, and the self-check itself must
    still pass with all its samples.
    """

    script = ROOT / "ops/service-host/cert-expiry-check.sh"

    without_marker = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "OWNED_DIRS_OVERRIDE": "/tmp/not-ours"},
    )
    assert without_marker.returncode == 2, without_marker.stdout + without_marker.stderr
    assert "CERT_EXPIRY_SELF_CHECK" in without_marker.stderr

    with_marker = subprocess.run(
        ["bash", str(script), "--self-check"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert with_marker.returncode == 0, with_marker.stdout + with_marker.stderr


def test_cert_expiry_gate_is_read_only() -> None:
    """It parses certificates. It must not write, copy, or reload anything.

    `--self-check` writes temporary sample certificates on purpose (it has to,
    to prove the gate rings). Everything outside that mode is asserted here.
    """

    script = _directives(EXPIRY_CHECK.read_text(encoding="utf-8"))
    body = script[: script.index("self_check()")]
    for forbidden in ("nginx -s", "systemctl", "install -m", "cp ", "mv ", "rm ", "mkdir "):
        assert forbidden not in body, f"gate is not read-only: {forbidden!r}"
