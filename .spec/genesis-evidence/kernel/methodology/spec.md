---
title: methodology
desc: 研究设计词表与"能否下因果结论"的推断约束
hue: 190
code:
  - src/genesis_evidence/core/methodology.py
---
# methodology

证据评价的分类学：研究设计词表、偏倚风险工具绑定，以及"这种设计允不允许因果表述"。

## raw source

设计词表曾以六种写法存在：抽取模型的 `Literal`、审核边界手抄的第二份 `Literal`、
`claim_reviews` 上的 SQL CHECK、历史重建迁移里的第二份 CHECK，以及工作台里的两处。六份副本里任何一份过期，
都会让一条动物实验或病例系列以"干预效果"的名义走到发布闸门前。

## 不变量

- `permits_causal_inference` 是唯一允许下因果判断的判定。
- 偏倚风险工具必须来自该设计被许可的集合；未许可的工具是错误，不是默认值。
- SQL CHECK 的字面量是已声明的例外：schema 字符串不能 import Python，改动时两处必须同提交。
