---
title: health-flow-adapter
desc: health-flow MetricRecord → Evidence API v3 的确定性适配器
hue: 290
code:
  - src/genesis_evidence/integrations/health_flow.py#build_evidence_request
---
# health-flow-adapter

上游 health-flow 的 `MetricRecord` 到 Evidence API v3 请求的形状转换。

## 不变量

- 必须显式传入 `confirmed=True`；未确认行只能进 `skipped`。默认值不得是"已确认"。
- 适配器只做形状转换与校验，不做匹配、不做措辞。
- 参考范围、数值、bbox 的解析失败显式落到无法判断，不猜。
