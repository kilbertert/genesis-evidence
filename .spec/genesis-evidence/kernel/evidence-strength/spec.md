---
title: evidence-strength
desc: 证据强度词表、排序，以及每种强度允许说什么
hue: 190
code:
  - src/genesis_evidence/core/evidence_strength.py
---
# evidence-strength

证据强度叫什么、怎么排序、每个值能跨过哪道门槛。

## raw source

"强度"曾同时写在 `core.contracts`、`core.store`、`core.matching`、`core.card_lifecycle`、
`review.service` 与工作台浏览器代码里。副本漂移的后果是**对患者说了不该说的话**：工作台告诉审核员
`low` 确定性会被扣下，但服务端放行了它。

## 不变量

- 患者可见集合、行动门槛集合、仅背景集合三者互不重叠且覆盖全部强度。
- 任何"这个强度够不够"的判断都查本节点导出的集合，不写字符串字面量。
- `mixed` 是聚合结果，不是可审核的强度值。
