# genesis-evidence feature map

One file per user-facing feature. Each answers: what it is, how to reach it, how to
drive it, and what observable end state proves it works.

The map is the maintained verification source. **A proof that drives one convenient
entry point is incomplete when this index lists others.**

## Canonical navigation

The workbench serves at `/` unauthenticated, but **nothing works until you
authenticate**. `#key` holds the API key, `连接` (`#connect`) verifies it via
`/api/review/me`, and a successful connect replaces the "未认证" label with the
reviewer id. There are two top-level views:

```js
// Served shell (no auth yet)
open('http://127.0.0.1:8126/')

// Pre-authenticate, then connect
sessionStorage.setItem('reviewKey', KEY);
document.getElementById('connect').click();        // -> /api/review/me 200

// 论文审核 view (paper queue, extraction jobs, per-paper review flow)
document.querySelector('[data-view="review"]').click();

// 疾病知识库 view (coverage matrix, knowledge cards, disease library)
document.querySelector('[data-view="disease"]').click();
```

**`/health` returning 200 proves only that uvicorn is up.** It says nothing about
the API key, and the workbench looks "broken" for the whole session if the key is
wrong. Always run the authenticated doctor probe first.

## Start here, not with a feature

An **empty throwaway database renders empty queues** — that is a correct render of
an empty database, not a verified feature. Before proving any review flow below,
either create the data through the API (see `tests/test_review_api.py` for the
exact call sequence) or state plainly that you verified an empty-state render.

| Feature | Surface | Reaches it by |
|---|---|---|
| [paper-admission](paper-admission.md) | 研究身份确认与论文准入 | 论文审核 → 选论文 → 准入/拒绝 |
| [extraction-jobs](extraction-jobs.md) | 抽取任务 / 重试失败阶段 | 论文审核 → 抽取任务 |
| [claim-card-review](claim-card-review.md) | Result 与 Claim 审核 / Evidence Profile | 论文审核 → 选论文 → 审核 |
| [disease-coverage](disease-coverage.md) | 疾病框架覆盖矩阵 / 知识卡 | 疾病知识库 |
| [evidence-api](evidence-api.md) | 只读已发布证据 API | `genesis-evidence-api` :8125 |

## Not on this map

- **论文全文抓取与 LLM 抽取** — calls an external model (`PAPER_AI_*`). Do not fake
  it; if you substitute its output, say so and do not claim the chain is verified.
- **health-flow 报告门户** — a different repo on :8127. See its `verify-healthflow`.
