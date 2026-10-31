# 21. The sandbox image is published to a registry, not built on each host

Date: 2026-10-31
Status: Accepted

## Context

`.sandcastle/profile.ts` named `sandcastle:genesis-evidence-governance`. That
name is satisfied by exactly one thing: a `docker build` someone ran on the
development host. Nothing in the system re-creates it.

- The weekly disk cleanup prunes the builder cache and never touches images.
- `upgrade-afk.sh` prints the rebuild command in its closing report. It is
  prose, not a check — a migration that skips it still stamps the new version.
- Sandcastle's own `checkImageUid` shells out to `docker image inspect` and, when
  that fails, reports `Image '...' not found locally. Build it first with
  'sandcastle docker build-image'`. **It does not pull.**

The failure this produces is expensive out of proportion to its cause. Measured
on this repository: `architecture-review` failed on **every weekday from
2026-10-17 through 2026-10-30**, fourteen consecutive runs. Each one got as far
as installing dependencies and then failed at `Run architecture-review.ts`,
several steps into the job, with a message about a missing image. Nothing about
that message says "an artifact was never built on this machine" — it reads as a
runner problem or a code problem, which is where two weeks went.

The asymmetry worth naming: the image is *rebuildable* but not *recoverable*.
There is no way to ask the system "does the thing my profile depends on exist?"
The only place that question is answered is inside the container-creation path of
a job that has already spent minutes getting there.

## Decision

The image is published to GHCR by `.github/workflows/sandbox-image.yml`, and
`profile.ts` names `ghcr.io/kilbertert/genesis-evidence-sandbox:latest`.

1. **The image is built by CI, on Dockerfile changes, and pushed.** A published
   image is re-pulled per job, so "the image is gone" stops being a state this
   host can reach.
2. **`architecture-review.yml` pulls it explicitly before the review step.**
   `checkImageUid` will not, so relying on the sandbox path to fetch it is
   relying on something that does not happen. Pulling separately also splits
   "the registry has no such image" from "the image lacks a tool the review
   needs" — two failures that were previously indistinguishable.
3. **The uid is stated on both sides.** A published image bakes one fixed uid;
   `docker()` otherwise defaults its expectation to `process.getuid()`. Those
   agree on this host today and that agreement is a coincidence, not a
   guarantee. `containerUid: 1000` and `containerGid: 1000` are passed
   explicitly, and the workflow bakes the same two numbers.

The package is public. Nothing secret is baked in — credentials are mounted at
run time, which is what the existing note in `profile.ts` is about — and a
private package would make every consuming job depend on a token to start.

## Consequences

- **A freshly scaffolded project has no image until the workflow has run on main
  once.** The pull has nothing to pull until then. `bootstrap-afk.sh`'s report
  now says so as step 1, because it is the one ordering constraint a new project
  can trip over.
- **The image is no longer reproducible from the host alone.** Rebuilding by
  hand still works, but it no longer changes what a job runs: `latest` in the
  registry does. This is the intended trade — a local build was the mechanism
  that failed silently.
- **A registry outage is now a sandbox outage.** Previously a stale local image
  would have kept running. That was the same property that made its loss
  invisible, so it is not a reason to prefer the old arrangement; it is a
  dependency worth naming.
- The `-governance` suffix in the old name is dropped. It distinguished the
  current image from two earlier generations that had accumulated on the host,
  a distinction that only exists when images live on a host.

## Alternatives considered

- **Keep the local build and add a pre-flight check that fails fast.** It answers
  "is the image there?" a few minutes earlier, but not "will it still be there?"
  — the rebuild stays a step a human must remember, which is the part that
  failed. Rejected as symptom treatment.
- **Make `upgrade-afk.sh` compare the image's creation time against the
  Dockerfile's mtime and fail on a mismatch.** This turns a silent failure into
  a loud one, and is genuinely better than nothing. It was rejected only because
  it still leaves the artifact on one host: the same check cannot help a job that
  runs where the image was never built, and it puts a Docker dependency into a
  script that otherwise only edits files.
- **Build inside every job.** No registry, no publication, always current. It
  costs the full build (measured locally: 51 s warm, minutes cold with no layer
  cache) on every review run, and the build itself needs network access to npm
  and apt that a review job should not require.
- **A shared image for all AFK projects.** Not rejected on merit — deferred. The
  Dockerfiles have diverged (chromium, Playwright, language runtimes), so sharing
  is a separate decision that needs its own evidence.

## What this record does not decide

Whether the other AFK projects move their images. `afk-bootstrap` ships this as
template version 1.7.38 and each project migrates on its own PR — the Dockerfiles
have diverged enough (chromium, Playwright, language runtimes) that a shared image
is a separate decision with its own evidence.
