---
title: report-store
desc: 报告域持久化——上传、确认、评估与无状态复算
hue: 40
code:
  - src/genesis_evidence/core/store/reports.py#ReportStore
---
# report-store

个人报告的存储侧：上传、确认、`assess`、以及无状态复算 `match_published_cards`。

## 不变量

- 只有 `confirmed` 的报告可被评估；确认后的观测才进入匹配。确认之前不产生任何判定。
- 确认只能纠正取值，不能新增未抽取的项目（`ConfirmationInput.require_only_corrected_values`）。
- `assess` 与 `match_published_cards` 必须产生同源结果；它们只差异于结果投影形状。
