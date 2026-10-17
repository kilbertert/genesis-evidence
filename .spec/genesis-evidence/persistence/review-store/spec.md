---
title: review-store
desc: 审核域持久化——主题台账、覆盖矩阵、知识卡与证据闸门
hue: 40
code:
  - src/genesis_evidence/core/store/review.py#ReviewStore._require_publishable
related:
  - src/genesis_evidence/core/store/card_evidence.py
  - src/genesis_evidence/core/store/published_cards.py
  - src/genesis_evidence/core/store/retirement.py
  - src/genesis_evidence/core/store/screening.py
  - src/genesis_evidence/review/api.py
  - src/genesis_evidence/review/workbench.html
---
# review-store

审核工作台的存储侧，也是**发布裁判**所在处。

## raw source

`_require_publishable` 是唯一的发布裁判：人工路径与自主路径共用它。如果有一条路径绕过它发布成功，
那么"闸门"这个词就不再成立——这比任何单条错误结论都严重。

## 不变量

- 单条 Claim 必须：审核 `approved`；论文完整性 `clear`、发表状态 `formal`、已内部准入、DOI 非空、全文已处理；
  `evidence_text` / `locator` 非空；Claim 类型为 `intervention_effect`；排除动物/体外/病例系列设计。
- 至少一条 Claim，每条 Claim 在 Profile 中有对应 Result，`scope_key` 非空。
- 上下文卡（`grade=low`）不得携带 `high`/`critical`/`uncertain` 的偏倚风险判定。
- 论文被重新评估时，其已发布卡片转 `stale` 并从患者接口消失。
- `_require_complete_topic` 是主题闭合检查：主题 locked、每个检索流有 completed run、无 running run、
  每条记录有筛选决定、纳入项有全文结果并完成抽取/准入/Claim 审核。
