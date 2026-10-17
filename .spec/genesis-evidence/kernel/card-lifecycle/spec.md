---
title: card-lifecycle
desc: 知识卡状态、合法迁移与"谁可以被患者看到"的唯一声明
hue: 190
code:
  - src/genesis_evidence/core/card_lifecycle.py
---
# card-lifecycle

知识卡生命周期是三件事：存在哪些状态、哪些迁移合法、其中哪些状态患者可见。

## raw source

这三件事曾分别声明在 `core.contracts`、`core.store` 与 `review.service` 里，Python 与工作台 JavaScript 各一份。
副本已经开始打架：覆盖统计曾悄悄漏掉 `rejected`。患者可见性是一句 SQL 片段，不是注释里的约定。

## 不变量

- `patient_visible_sql(column)` 是唯一生成患者可见性谓词的地方；写裸 SQL 的可见性条件属于缺陷。
- 终态集合与 [[evidence-strength]] 里"患者可见等级"必须一致——强等级不可见的卡片，其状态也不得可见。
