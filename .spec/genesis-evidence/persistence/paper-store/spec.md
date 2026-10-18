---
title: paper-store
desc: 论文域持久化——采集、全文对象、抽取任务、准入、Claim、Result
hue: 40
code:
  - src/genesis_evidence/core/store/papers.py#PaperStore
related:
  - src/genesis_evidence/literature/models.py
  - src/genesis_evidence/literature/backlog.py
  - src/genesis_evidence/literature/topic_plans.py
---
# paper-store

论文证据生产线的存储侧（19 张表里的主体）。

## 不变量

- 任务领取依赖 `paper_extraction_jobs_active_paper_unique` 部分唯一索引：同一论文最多一个 queued/running 任务。
  并发下多领一份会让同一篇论文被抽取两次并产生两份 Claim。
- 全文对象以内容哈希 + SHA-256 存对象存储，不存进库。
- 撤稿是显式失败路径（`PaperRetracted`），不是"跳过"。
- `list_unscreened_papers` 是 `list_pending_full_texts` 的对偶：前者选
  `title_abstract_decision IS NULL`，后者选 `'included'`。**两者都是完整队列生命周期的
  一部分**——只看后者会让「从未筛过」的论文同时缺席检索队列与抽取队列（抽取作业只从
  `queued` 领取，而未筛论文没有作业）。`literature/backlog.py` 用前者驱动筛查，
  再跑后者；顺序不可颠倒，因为检索要求已判为 `included`。见 ADR 0015。
