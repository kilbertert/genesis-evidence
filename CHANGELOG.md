# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Bootstrap evidence core (#1)
- Port literature acquisition core (#2)
- Persist literature evidence candidates (#3)
- Add reviewed AI paper extraction (#4)
- Add evidence review and card publication (#5)
- Add evidence review workbench (#6)
- Add pending report extraction (#7)
- Add report confirmation workflow (#8)
- Match confirmed reports to published cards (#9)
- Add personal report portal (#10)
- Align literature review with evidence profiles (#14)
- Persist literature extraction jobs (#15)
- Automate auditable evidence review (#28)
- Add Health-Flow evidence matching boundary (#30)
- Complete evidence canary workflow (#32)
- Add disease coverage matrix (#39)
- Publish layered evidence context cards
- Support non-HDL cholesterol evidence
- Group patient findings by health condition
- Add workflow_dispatch trigger to agent-review
- Plan A — no auto-claim on agent:implement label
- Plan A full decouple — implement-prd dispatch-only
- Close reference gaps — dependency analysis + model guardrails
- Strip queue labels on issue close
- Add product catalog data layer
- Add acceptance contract governance and product catalog data layer
- Deliver reviewed recommendation chain (#116)
- Enforce governance contract
- Add images to published recommendations (#125)
- Add disease knowledge library tab to workbench (#127)

### Changed
- Tighten product catalog guard and idempotency coverage
- Deepen profile-scope resolution into one stateless module (#112)
- Deepen published card matching (#113)

### Documentation
- Document deployed URLs, access, and service topology (#18)
- Record old genesis-health services stopped (#19)
- Add product recommendation acceptance and governance baseline
- Align product acceptance traceability (#117)
- Record canonical product e2e (#118)
- Update mutation test totals (#120)
- Record real five-image report acceptance
- Add real acceptance traceability
- Record final real image acceptance (#123)
- Retain review workflow boundaries (#135)
- Correct the user portal routing in the service README (#140)
- Rewrite the README as a system-design entry point (#142)
- Retire the FRP entry and describe the service host topology (#158)

### Fixed
- Use isolated runtime ports and module entrypoints (#13)
- Fail fast when ARK_API_KEY is missing (#16)
- Give the Ark reasoning model enough output budget (#17)
- Make real acceptance states diagnosable (#20)
- Bound consistency output budget (#21)
- Process report extraction in background worker (#22)
- Extend report timeout and improve workbench feedback (#23)
- Streamline abnormal report confirmation (#24)
- Target invalid evidence corrections (#25)
- Include JATS review statements (#26)
- Track full-text retrieval outcomes (#27)
- Close excluded papers without extraction (#29)
- Enforce phase-one evidence service boundary (#33)
- Enforce evidence boundary and close stale review backlog (#34)
- Hide Evidence API schema (#35)
- Reconcile topic screening ledgers
- Support compatible paper ai providers (#37)
- Configure provider request timeout
- Prioritize targeted extraction batches (#40)
- Normalize legacy consistency issues (#41)
- Balance targeted extraction by disease (#42)
- Re-evaluate bilingual PICOTS screening (#43)
- Continue autonomous review after extraction (#44)
- Match governed coverage outcomes
- Preserve metric card findings (#48)
- Negotiate response schema version
- Keep unprofiled topic records screenable (#51)
- Reconcile profiled duplicate ledger rows
- Preserve long disclosure fields
- Make evidence publication migration safe
- Adjudicate source-backed extraction differences
- Unblock nutrition evidence scope
- Tolerate inline source citations
- Ignore reviewed out-of-scope claims
- Preserve extraction context in coverage scopes
- Retry invalid correction output
- Isolate serum creatinine outcome scope (#61)
- Split compound kidney outcomes (#62)
- Recognize muscle mass aliases (#63)
- Separate BMD and T-score evidence scopes
- Recognize defined nutrition components (#65)
- Exclude rejected results from profiles (#66)
- Split legacy shared Result identities (#67)
- Reopen AI rejection after new topic inclusion
- Match chinese lipid interventions (#69)
- Use genesis-evidence's own sandbox image instead of Auto-Test's
- Unescape GH_REPO in manual checkout steps
- Clean stale agent branches in runner checkout
- Add zod dependency
- Update package-lock with zod
- Add pull_request labeled trigger for agent-review
- Remove orphan step stubs from label workflows
- Allow workflow_dispatch through agent-review gate
- Use npm cache in setup-node
- Inject GH_TOKEN into the sandbox env
- Grant issues permission to PR-label workflows
- Repair architecture-review workflow YAML
- Make architecture-review checkout idempotent
- Align label lifecycle with reference
- Align label-Action review with the reference design
- Improve workbench reading experience (#128)
- Use host delivery token for pull requests (#130)
- Deepen review checkout history (#131)
- Harden AFK review delivery
- Fail closed on workflow boundaries
- Handle pull requests without linked issues (#134)
- Apply provider-neutral economy contract (#136)
- Align deployment docs with observed runtime (#141)

