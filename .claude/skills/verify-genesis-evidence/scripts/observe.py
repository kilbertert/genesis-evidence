#!/usr/bin/env python3
"""Launch a genesis-evidence service against a throwaway sandbox and capture evidence.

Verification scaffolding, not product code. It exists so a cold-reading agent cannot
accidentally point a verification run at the working database: this script always
creates a fresh temp database and object store, passes them as environment
overrides, and removes them on the way out.

    uv run python .claude/skills/verify-genesis-evidence/scripts/observe.py \
        --service review --out var/verify-evidence

The service's own `var/*.env` supplies credentials and model endpoints; only the
database and object paths are overridden. Evidence survives teardown by design.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVICES = {
    "review": {
        "entrypoint": "genesis-evidence-review",
        "env_file": "var/review.env",
        "port_var": "GENESIS_EVIDENCE_REVIEW_PORT",
        "default_port": 8126,
        # The workbench page is unauthenticated; an authenticated probe proves the
        # key in var/review.env is the key this instance actually expects.
        "doctor_path": "/api/review/me",
        "auth_var": "GENESIS_EVIDENCE_REVIEW_API_KEY",
    },
    "api": {
        "entrypoint": "genesis-evidence-api",
        "env_file": "var/portal.env",
        "port_var": "GENESIS_EVIDENCE_PORT",
        "default_port": 8125,
        "doctor_path": "/health",
        "auth_var": None,
    },
}


def read_env(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE file. Values may be quoted; comments and blanks ignored."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip().strip('"').strip("'")
    return values


def http_status(url: str, token: str | None = None, timeout: float = 3.0) -> int:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def wait_for_port(url: str, deadline: float, token: str | None = None) -> bool:
    while time.monotonic() < deadline:
        if http_status(url, token):
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=sorted(SERVICES), default="review")
    parser.add_argument("--out", default="var/verify-evidence", help="where evidence lands")
    parser.add_argument("--keep-sandbox", action="store_true", help="keep the temp DB for triage")
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--startup-timeout", type=float, default=40.0)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[4]
    spec = SERVICES[args.service]

    env_file = repo / spec["env_file"]
    if not env_file.is_file():
        example = Path(spec["env_file"]).name
        print(
            f"error: {env_file} is missing; the service cannot start without it.\n"
            f"  {spec['env_file']} is gitignored, so a fresh worktree has none.\n"
            "  Copy it from the canonical checkout (it holds the review API key\n"
            "  and the model endpoints):\n"
            f"    cp /home/claude/Projects/genesis-evidence/{spec['env_file']} "
            f"{spec['env_file']}\n"
            f"  For a new environment, start from ops/examples/{example}.example\n"
            "  Never commit it, and never point a verification run at\n"
            "  var/genesis-evidence.sqlite3.",
            file=sys.stderr,
        )
        return 2
    env = {**os.environ, **read_env(env_file)}

    port = int(env.get(spec["port_var"], spec["default_port"]))
    base = f"http://127.0.0.1:{port}"

    sandbox = Path(tempfile.mkdtemp(prefix=f"genesis-verify-{args.service}-"))
    env["GENESIS_EVIDENCE_DATABASE"] = str(sandbox / "evidence.sqlite3")
    env["GENESIS_EVIDENCE_OBJECTS"] = str(sandbox / "objects")
    env["PYTHONPATH"] = f"{repo / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)

    print(f"sandbox: {sandbox}")
    print(f"service: {spec['entrypoint']} -> {base}")

    process = subprocess.Popen(
        ["uv", "run", spec["entrypoint"]],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )

    try:
        if not wait_for_port(f"{base}/health", time.monotonic() + args.startup_timeout):
            print("error: service did not become ready", file=sys.stderr)
            return 1
        print("  health: up")

        token = env.get(spec["auth_var"]) if spec["auth_var"] else None
        status = http_status(f"{base}{spec['doctor_path']}", token)
        print(f"  doctor {spec['doctor_path']}: {status}")
        if status == 401:
            print(
                "  -> 401: the key in "
                f"{spec['env_file']} is not the key this instance expects. "
                "That is an auth mismatch, not a broken app.",
                file=sys.stderr,
            )
        elif status != 200:
            print("  -> doctor did not return 200; a drive would be unreliable", file=sys.stderr)

        if not args.no_screenshot:
            out_dir = (repo / args.out).resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
            shot = out_dir / f"{args.service}-root.png"
            chrome = shutil.which("google-chrome") or shutil.which("chromium")
            if chrome:
                subprocess.run(
                    [
                        chrome,
                        "--headless=new",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--window-size=1440,900",
                        "--virtual-time-budget=6000",
                        f"--screenshot={shot}",
                        f"{base}/",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=60,
                )
                if shot.exists():
                    print(f"  evidence: {shot.relative_to(repo)}")
                else:
                    print("  evidence: screenshot failed", file=sys.stderr)
            else:
                print("  evidence: no chrome on PATH; skipped screenshot", file=sys.stderr)

        return 0
    finally:
        # Kill what we started, never by name: the same entrypoint may be a systemd
        # unit on this host, and pkill would take that down too.
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        if args.keep_sandbox:
            print(f"sandbox kept: {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)
        print("done (evidence is outside the sandbox and survives)")


if __name__ == "__main__":
    raise SystemExit(main())
