---
title: synthesis-eligibility
desc: 哪些结果可以进成分级证据合成，为什么由名称决定
hue: 190
code:
  - src/genesis_evidence/core/synthesis_eligibility.py
---
# synthesis-eligibility

"一条结果能不能进成分级合成"的唯一规则（ADR 0007）。成分身份由干预**名称**决定，因此判定是精确且 fail-closed 的。

## raw source

名称不是全部真相：一个名称只含单一成分的结果，也可能描述"不只是这个成分"的干预。目录里记录的
维生素 D 案例就是这样——同一名称下有一行其实是复方。不可合并的干预仍进完整证据体，但不参与合成。

## 不变量

- `poolable` 由解析结果决定，不由调用方传入。
- 确定性判定来自目录声明，**不得**用自由文本长度之类的启发式代替。
- 排除是一等结果，要能被审核员看见，而不是静默丢弃。
