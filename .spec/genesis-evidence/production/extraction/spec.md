---
title: extraction
desc: 论文事实抽取与独立一致性裁决
hue: 150
code:
  - src/genesis_evidence/literature/ai_extraction.py
---
# extraction

Ark 驱动的论文事实抽取，随后跑一次**独立**的一致性检查。

## raw source

"独立"是这个词的全部意义：两次抽取用不同的输入顺序或不同的提示，让分歧暴露出来，
而不是让第二次复述第一次。分歧进人工队列，见 [[consistency]]。

## 不变量

- 抽取只产出候选事实与原文位置，不产出结论、不产出诊断。
- 抽取失败必须落成可重放的状态（job 表），不能只写在日志里。
