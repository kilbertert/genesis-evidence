---
title: review-scope
desc: PICOTS 匹配与 Evidence Profile 稳定结果范围解析
hue: 150
code:
  - src/genesis_evidence/review/scope.py#EvidenceProfileScopeResolver
---
# review-scope

Evidence Profile 的"稳定结果范围"判定：首批报告指标解析到 `metric:<code>`，无指标的 condition 解析到
`condition:<code>`，其余按锁定的 PICOTS outcome 文本派生成 `outcome:<slug>`。

## raw source

本模块**不得** import `genesis_evidence.review` 包：该包 `__init__` 会拉起 [[review-service]]，
进而拉起存储层，与 `core.store → review.scope` 构成环。这条约束写在文件 docstring 里，
改 import 之前先读它。

## 不变量

- 无状态、无存储依赖：只吃锁定的 PICOTS、condition 定义、指标标签与归一化输入。
- 解析结果必须确定性：同一输入两次解析得到同一 `scope_key`。
