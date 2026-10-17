---
title: metrics
desc: canonical 指标标识、别名归一化与"证据必须字面出现"的判定
hue: 190
code:
  - src/genesis_evidence/core/metrics.py
---
# metrics

报告项目名到 canonical `metric_code` 的解析，以及证据文本与数值的字面匹配。

## raw source

别名表是名称解析的**唯一一处**。患者侧匹配只认 `metric_code`，不认报告上印的自由文本项目名；
解析不了的项目显式落入 `unknown_metric_code`，**不得静默归属**到任何 condition——
静默归属会产生凭空的因果，而"未识别"是诚实的结果。

## 不变量

- `evidence_contains_value` 就是"证据必须字面出现"这句话本身；放宽它等于允许编造引用。
- 别名表由 canonical 标识与标签共同派生，不手工维护第二份。
