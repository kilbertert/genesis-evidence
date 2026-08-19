# Evidence governance standards mapping

This stage uses the following standards only for the observable evidence-review
contract. It does not claim that the product itself is a completed systematic review.

| Source | Requirement used here | Concrete mapping |
| --- | --- | --- |
| PRISMA 2020 flow diagram and checklist | Report records identified, screened, sought for retrieval, not retrieved, assessed for eligibility, included and excluded, with reasons | `collection_runs` records each source/search stream; `collection_papers` records title/abstract decisions, `pending`/`retrieved`/`not_retrieved` full-text retrieval with an audit reason, and separate scientific full-text decisions plus one primary exclusion reason |
| Cochrane Handbook chapter 4, especially MECIR C41 | Predefine eligibility, document selection for all identified records, retain explicit full-text exclusion reasons, and collate multiple reports by study | Locked `evidence_topics` stores the review question, PICOTS, eligible designs and criteria; DOI/PMID/PMCID deduplication and `studies`/`study_publications` keep publication and study identities separate |
| AHRQ Evidence-based Practice Center PICOTS framing | Define Population, Intervention or Exposure, Comparator, Outcomes, Timing and Setting before selection | `evidence_topics.picots_json` requires those six named fields before a topic can be created and locked |
| RFC 6750 section 2.1 | Send bearer credentials in the HTTP `Authorization` header; reject invalid credentials with a Bearer challenge | Review endpoints accept only `Authorization: Bearer <token>`, return `WWW-Authenticate: Bearer` on 401, and derive the audit actor from server-side `GENESIS_EVIDENCE_REVIEWER_ID` |
| Volcengine Ark Chat API | `stream=true` returns incremental Chat completion events | Long-form paper extraction consumes the provider's `data:` event stream and preserves the provider request ID while assembling the same JSON response contract |
| NISO JATS 1.3 Journal Publishing Tag Library | Funding is represented in `article-meta/funding-group`; article declarations may be represented as footnotes under `author-notes` or the back-matter `fn-group` | `JatsDocument.statements` includes funding groups, author-note footnotes, acknowledgements and direct back-matter footnotes; table footnotes and bibliographic references are excluded from this review-fact channel |
| SQLite transactions and partial indexes | `BEGIN IMMEDIATE` starts the single write transaction immediately; an index `WHERE` clause limits entries to matching rows | Job claiming uses the existing immediate transaction boundary, and a partial unique index permits at most one queued/running extraction job per paper |
| systemd service restart policy | `Restart=on-failure` is recommended for long-running services | The user-level extraction worker restarts after unclean exit; inherited running jobs become explicit interrupted failures and retain every previously persisted stage |

The Evidence Profile completeness flag is system-derived. Creation is blocked unless the
topic is locked, every required search stream has a completed run, every discovered record
has the required screening decisions, every exclusion has one reason from the locked
catalogue, every title/abstract inclusion has a completed full-text retrieval outcome, and
every full-text inclusion has completed extraction, internal admission and
Claim review. TLS termination remains the deployment boundary required to protect bearer
tokens in transport.

An Evidence Profile is grouped by a stable outcome scope within one locked topic. When the
outcome is a first-batch report metric, the scope key is its canonical `metric_code`;
otherwise it is derived from the locked PICOTS outcome text. Study-specific population,
form, dose, comparator wording and timepoint remain attached to each Result and do not
split semantically equivalent outcome evidence into separate profiles.

The first-stage product intentionally uses one authenticated human reviewer. It therefore
does not implement Cochrane MECIR C39's two-person independent full-text selection and must
not be represented as a Cochrane review. The persisted decisions and reasons preserve the
audit trail needed to add an independent second selection later if that scope is approved.

Authoritative sources:

- <https://www.prisma-statement.org/prisma-2020-flow-diagram>
- <https://www.prisma-statement.org/prisma-2020-checklist>
- <https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-04>
- <https://effectivehealthcare.ahrq.gov/>
- <https://www.rfc-editor.org/rfc/rfc6750.html>
- <https://www.volcengine.com/docs/82379/1494384>
- <https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/funding-group.html>
- <https://jats.nlm.nih.gov/publishing/tag-library/1.3/element/fn.html>
- <https://www.sqlite.org/lang_createindex.html>
- <https://www.sqlite.org/lang_transaction.html>
- <https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html>
