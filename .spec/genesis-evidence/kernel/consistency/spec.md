---
title: consistency
desc: 双抽取一致性裁决的词表与 provenance
hue: 190
code:
  - src/genesis_evidence/core/consistency.py
---
# consistency

"两次独立抽取是否一致"的词表，以及裁决来源的声明。

## raw source

`'consistent' | 'needs_review'` 曾同时写在抽取报告的模型里和 `consistency_status` 列的 CHECK 里。
Python 侧收进本节点；SQL CHECK 保留字面量，因为 schema 字符串不能 import 一个常量。

## 不变量

- 一致性裁决必须留下 provenance：哪一次抽取、谁裁的。
- 不一致不等于废弃——它把论文送进人工审核队列，而不是丢弃。
