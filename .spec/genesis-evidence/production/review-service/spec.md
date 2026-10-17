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
- **自主环节只能翻掉自己未经核实的决定。** 两道 Claim 重开闸门（`ai_rejection_reopened`、
  `ai_approval_recheck`）问的是 `is_autonomous_reopenable`，**不是** `is_machine_actor`：
  `source-ai:` 是机器，但它的拒绝已对照全文核实，且 `source_based` 决议落库后
  `_source_adjudication` 会被跳过——重开即等于"在没重新核实的那一趟里翻案"，因此必须被拒绝。
  一条路径若把这两个谓词用反，表现为"第二趟自动审核静默改判"。见 [[consistency]]。
- **筛查先于“判定卡住”。** 没有全文的论文走不到抽取阶段，而筛查循环原先只在抽取
  产生 trace 之后才可达——于是“无全文 → 不筛查 → 不检索 → 无全文”互相等待。
  现在 `if not trace` 分支在返回 `attention_required` **之前**先消费筛查建议，
  且这条路径的 actor 是 `ai:screening-ledger`（题录建议由采集记录确定性派生，没有模型参与）。
  见 [[review-store]] 与 ADR 0015。
- **不产出过不了自己前提的建议。** 全文裁决建议要求 `full_text_retrieval_status == 'retrieved'`
  （否则 store 必拒）且研究设计已知（`design != 'uncertain'`）。用尚未测量的设计去排除一篇论文，
  是拿缺失数据下科学结论——比保持台账打开更糟。
