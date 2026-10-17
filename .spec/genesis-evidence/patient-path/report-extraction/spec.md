---
title: report-extraction
desc: 有序多模态报告抽取——停在用户确认之前
hue: 290
code:
  - src/genesis_evidence/reports/extraction.py#HealthReportExtractor
---
# report-extraction

把一份体检报告（图片或 PDF）抽成候选观测，然后**停下**。

## raw source

抽取不产生诊断，只产生候选观测与原文证据。这条边界不是保守，而是责任划分：
模型说"这里有一行血脂 5.2"，用户确认"是的，这是 5.2"，之后才进入匹配。

## 不变量

- 抽取结果必须可被用户逐项确认或纠正；不允许"整体接受"。
- 每条候选观测都必须带原文位置（bbox 或页内偏移），否则不能进入确认界面。
- `_migrate`/重新导出 `evidence_contains_value` 是已声明兼容债，见 [[evaluation]]。
