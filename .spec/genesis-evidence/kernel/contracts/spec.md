---
title: contracts
desc: 对外请求/响应模型与卡片能力分层
hue: 190
code:
  - src/genesis_evidence/core/contracts.py
---
# contracts

v2/v3 请求响应模型，以及"已发布"与"能说什么"这两个独立决定中的后者。

## raw source

发布 ≠ 能力。任何已发布卡片（含 `moderate`/`high`）都返回 `content_layer=context_only`、
`action_status=not_available`，直到存在**单独审核过**的行动内容。这条决定保护的是患者：
证据支持"存在关联"，不等于支持"该怎么做"。

## 不变量

- 请求 `schema_version` 只接受 `"2"` / `"3"`，未知版本是错误而非降级。
- v2 保持扁平旧形状，以便调用方独立切换；改 v3 时不得顺手改 v2。
- 能力摘要由 [[evidence-strength]] 的集合派生，不写第二份词表。
