---
title: evidence-store
desc: 对外只读匹配——只读已发布卡片与已发布产品，写审计
hue: 40
code:
  - src/genesis_evidence/core/store/evidence.py#EvidenceStore.match_published_cards
---
# evidence-store

health-flow 使用的只读视图。它的"只读"是契约的一部分，不是实现细节。

## 不变量

- 可见卡片条件：`status='published' AND grade IN (high, moderate, low)`，经 [[card-lifecycle]] 的谓词生成。
- 本节点不写业务状态，只写审计。
- 三个匹配入口之间不得出现语义分歧；差异只能来自 [[matching]]，不能来自这里的投影代码。
