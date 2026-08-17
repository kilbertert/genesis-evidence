# Phase 3 baseline audit

Date: 2026-08-17 (Asia/Shanghai)

## Git baseline

- Canonical checkout: `/home/claude/Projects/genesis-evidence`
- Default branch: `main`
- Base commit: `a7878c56df1750e9a3ba3927a3dc44a80b66ef87`
- Task branch: `feat/health-flow-evidence`
- Task worktree: `/home/claude/Projects/.genesis-evidence-phase2`
- `origin/main` was fetched and was an ancestor of the local `main`.

The canonical checkout had no tracked modifications. Its only untracked files
are user-owned acceptance assets and are intentionally retained:

- `体检报告/` (five de-identified JPG files)
- `全球营养成分基础知识库_检索筛选抽取审核与入库方案_纯文字版.docx`

The previous `fix/standardize-literature-review` worktree contains uncommitted
legacy implementation changes. It was marked preserved with `dev-worktree` and
is not used by this stage.

## Data backup

Before stage-3 work began, SQLite's online backup command created:

`var/backups/genesis-evidence.before-phase-3-architecture.20260817T083234Z.sqlite3`

SHA-256:

```text
e854d6084c14534e098c5936fa5d1c5ea8812053c5223d82d9152a312cca7408
```

The live database checksum at the same audit point was:

```text
99a85c2d3962a614a27c9e7f380e7e262e567a2c096537269f901a121f5d67d7
```

The backup is ignored runtime state and is not committed.

## User-asset hashes

These hashes identify the preserved local acceptance inputs without copying
their contents into Git:

```text
be227c65328b22d882fe9930fdb94fffc3d717c1c22b7de5960ff4824f5d9083  体检报告/49e587c1-dd2f-4aa9-95c3-e1c61ee7f20e.jpg
649bd0370765a3e85d9b2cff7a2aa5c6074b5f45520cfc1383105f3952bc5278  体检报告/61258614-f868-481b-97f5-693684b73d3b.jpg
5429487f2021f846eb04057acc60489adf1aecd6b6225ae7775d4d0dbff9c277  体检报告/97e7a42c-24f4-4d83-8a65-f733e3bbbbf1.jpg
519159674835017a4e697aa5f6cf3031ac5984721765f68def001d48b4fc199c  体检报告/9aa6e85e-2a88-4ce0-98dd-45f47a97db4c.jpg
9c8b6d555773fee4dde575d9dd9eef19f48ff41581efc0bbb38f2b8e9477c42d  体检报告/d96d920f-8226-4197-8b0d-38bf579ffb09.jpg
21025b000043bfa0fe3434cf025e7e94510c78088379c2f655ac56d0a0cdd159  全球营养成分基础知识库_检索筛选抽取审核与入库方案_纯文字版.docx
```

## Frozen scope

This stage changes only the evidence-service integration contract. The old
portal, Health-Flow source, host services, Nginx, FRP, and production data are
not deleted or modified by the baseline step.
