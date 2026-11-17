# Domain docs

This repository uses a single domain context. Read `GLOSSARY.md` for glossary
terms only; it must contain definitions and avoided synonyms, never a spec or
implementation decision.

Read relevant `docs/adr/` records for implementation decisions and boundaries.
Requirements belong in a spec or issue. Execution slicing and blocking edges
belong in native tickets.

## Task sources and documentation

Workflow references to `docs/` mean the relevant entries below, not a full-directory read.

| Question | Read first |
|---|---|
| Terms and product safety boundary | [GLOSSARY](../../GLOSSARY.md), [README](../../README.md) |
| Approved observable behavior and verification | Relevant [.spec](../../.spec/) node, [Feature](../../acceptance.feature), [QA](../../qa-plan.md); link overlapping rules instead of copying them |
| Why a choice was made | The relevant [ADR](../adr/); verify its status and successor |
| How to deploy or recover | [Deployment guide](../deployment.md), [service-host assets](../../ops/service-host/README.md) |
| What actually runs or passed acceptance | The corresponding deployment/verification event, with revision, environment and scope; prose in a design or README is not live evidence |

Before material changes, record the proposed delta and acceptance in the existing
task and affected contract. Trivial reversible edits need no separate brief.
New plan, progress or evidence files need a distinct audience, purpose or lifecycle
explained in the task; an extra link or repeated status is insufficient. Keep progress
in the original issue and one record for each distinct verification event. Preserve
failed historical evidence and private operational records.

Update current guidance in place and identify superseded decisions. Use llmdoc
as a route to sources, not another authority or proof of deployment; source changes
trigger review, not automatic rewrites. These are documentation maintenance rules,
not changes to patient safety, evidence admission or publication gates. No new
framework, status dashboard or whole-directory reading requirement is introduced.

Acceptance for this routing change: from AGENTS and this table, locate the patient
contract, its QA steps and the deployment procedure; distinguish a proposed topology
from runtime evidence. A typo reuses the PR, while a separate deployment keeps its
own evidence. Link results in the PR; do not create an additional rollout journal.
