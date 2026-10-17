---
title: matching
desc: 已确认观测 → finding 的唯一确定性匹配入口
hue: 190
code:
  - src/genesis_evidence/core/matching.py#EvidenceMatcher
---
# matching

把一条已确认的观测投影成 finding，再按 scope 严格度挑出可发布的卡片。这是"证据能不能到达患者"的唯一判定处。

## raw source

同一份匹配语义有三个薄调用者：`EvidenceStore.match_published_cards`、`ReportStore.match_published_cards`、
`ReportStore.assess`。它们之间的差异**只允许**是卡片来源、scope 严格度、结果投影形状。任何第四条分支属于语义，
必须落在这里而不是调用者里。

## expanded spec

`EvidenceMatcher` 持有 `scope_key` 与 `match_published_cards`；`project_observation` 把请求观测转成匹配输入，
`validate_observation` 在入口拒绝非法形状。等级不同的 finding 报 `evidence_strength="mixed"`，绝不塌缩成单一等级
——这条规则属于本节点，不属于调用者的投影代码。

## 不变量

- 匹配只认 canonical `metric_code`，不认报告上的自由文本项目名。
- 未匹配的原因必须可分类，交给 [[disposition]] 决定患者读到的措辞。
