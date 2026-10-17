---
title: patient-path
desc: 主线 B——一条已确认观测怎样变成患者读到的一句话
hue: 290
---
# patient-path

从 health-flow 的已确认观测到患者侧响应。

## 为什么单独成层

这条路径与论文生产线**共用同一个证据体**，但风险不同：论文线走一次要几小时，报告线走一次要几秒，
而且直接对患者说话。所以这条线上的每一处措辞都被当作契约而不是文案。

## 端到端

health-flow 确认观测 → 每个 metric 独立走一遍 [[matching]] → 同一 condition 的多个 metric 聚合成一个 finding
（每个保留自己的 evidence item）→ 等级不同的 finding 报 `mixed`，绝不塌缩成单一等级 →
未覆盖的进 `unmatched`，正常或证据不足的进 `skipped`。

响应到 finding 为止：**商品不在其中**。患者侧商品由商城按 `condition_code` 与标签映射另行提供（ADR 0006）。
