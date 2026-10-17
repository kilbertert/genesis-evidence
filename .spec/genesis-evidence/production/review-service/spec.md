---
title: review-service
desc: 自主论文审核编排——筛选终结、身份准入、Claim 审核、建档
hue: 150
code:
  - src/genesis_evidence/review/service.py#EvidenceReviewService
---
# review-service

自动审核路径的编排。它做的每一件事，人工也能做；两者写同一套 store，产出的审计记录事后**无法区分**谁做的。

## raw source

这个"无法区分"被当作缺陷而不是特性：AI 自动执行与人工覆盖产出的都是带 actor 的审计记录。
一个自动决定必须能回答"依据哪个策略版本、谁请求的"。

## 不变量

- 每个自动决定写 `policy_version`（当前 `literature-review-ai/1.5`）与 `requested_by`。
- 自动路径与人工路径共用 [[review-store]] 的发布裁判，不各自实现。
- 自动审核**不能**放宽任何一道闸门；它只能加快人类本会做的决定。
