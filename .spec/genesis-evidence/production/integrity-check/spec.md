---
title: integrity-check
desc: 发表完整性核查——撤稿与更正
hue: 150
code:
  - src/genesis_evidence/literature/integrity.py#IntegrityAssessment
---
# integrity-check

在论文进入证据体之前核查它是否被撤稿或有更正。

## 不变量

- 核查结论落成 [[publication-integrity]] 的状态，不另造词表。
- 核查失败（网络、上游不可达）得到 `unknown`，**不等于** `clear`；`unknown` 不放行。
- 核查结果与核查时间一并持久化，供审核员判断新鲜度。
