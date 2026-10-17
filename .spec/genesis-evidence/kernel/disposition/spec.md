---
title: disposition
desc: 观测为什么没成为 finding，以及这件事怎么告诉患者
hue: 190
code:
  - src/genesis_evidence/core/disposition.py
---
# disposition

"为什么这条观测没有变成 finding"与"患者因此读到什么"是同一个决定的两面，必须同源。

## raw source

它曾是四份副本：一个契约 `Literal`、两套互不相交的生产者词表、三个读取谓词、三个患者回复构造器。
副本在两个方向上散开，结果是在一条本该收口的路径上产生了**活着的假安慰**——患者读到"一切正常"，
而实际原因是证据不足。

## 不变量

- `normal`（确实正常）与 `unusable`（无法判断）在患者侧必须产生不同措辞；合并这两者是最严重的一类缺陷。
- 跳过原因的排序（`urgency_rank`）决定患者先看到哪个说明。
- 未识别的指标名进 `unknown_metric_code`，不得静默归属到任何 condition。
