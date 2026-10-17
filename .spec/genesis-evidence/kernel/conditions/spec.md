---
title: conditions
desc: 健康问题目录——可审核问题的唯一定义源
hue: 190
code:
  - src/genesis_evidence/core/conditions.py
related:
  - src/genesis_evidence/core/metrics.py
---
# conditions

首批健康问题的目录：`condition_code` → 指标集合 + 科室 + 复查方向。

## raw source

`condition` 是可审核问题的锚点，**它可以没有对应的报告项目**——报告异常不是它成立的前提。
它能不能到达患者，由"可触达"单独判定，不由目录是否存在决定。

## 不变量

- 目录在 `Database.initialize()` 时 upsert 进 `conditions` 表，是唯一可写来源；别处写目录行属于缺陷。
- 指标集合为空且无其他触达方式的 condition 患者侧不可达——这是状态，不是缺陷。
- "健康方向"（补充剂功能分类）**不是** condition 的同义词，也不得由它推导 condition。
- 指标身份只用 [[metrics]] 里的 canonical `metric_code`。
