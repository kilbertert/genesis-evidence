---
title: production
desc: 论文证据生产线的编排——检索、抽取、一致性、准入、审核、建档
hue: 150
---
# production

主线 A 的编排层：把 [[kernel]] 的判定与 [[persistence]] 的存储串成一条可重放的流水线。

## 为什么这层存在

每一步都是一次**可重放的状态**，不是一次函数调用。论文可能在抽取阶段失败、在一致性阶段被送回人工、
在准入阶段被拒；任何一步崩溃后都必须能从已持久化的状态继续，而不是从头再来。

## 已知的回边

`literature.extraction_worker → review.service`（每阶段结束调用自动审核）与
`review.service → literature.{ai_extraction, jats}`（需要研究设计枚举与 JATS 事实）构成一对回边。
它们被解释过、被接受，但它们意味着这两块不能独立演进——改动其中一侧前先确认另一侧的使用点。
