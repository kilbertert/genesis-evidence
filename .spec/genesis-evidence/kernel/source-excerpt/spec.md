---
title: source-excerpt
desc: 从论文全文里取原文摘录的边界
hue: 190
code:
  - src/genesis_evidence/core/source_excerpt.py
---
# source-excerpt

患者侧回链用的原文摘录：取什么、留多长、怎么定位。

## raw source

摘录是"可追溯"这句话的具体兑现。患者点开一条结论，应该看到他读到的那句话在论文里的位置，
而不是一段由模型转述的文字。

## 不变量

- 摘录必须是原文的连续片段，不得改写、拼接或补全。
- `locator`（章节/段落/区间）缺失时，结论不能走到发布闸门前。
