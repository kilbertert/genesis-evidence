# 未覆盖提示为什么在本仓只做一半（2026-10-07）

T9 的目标是 PRD #239 的第 12 条 user story：报告里每一项异常都要有明确归属——
要么对应一个健康问题，要么显式告知不在当前解读范围内。**前半已完成，后半有一部分
不在本仓。** 本记录把边界写清楚，免得后来者以为已经做完。

## 一、两段链路

```
报告行 ──► [health-flow] build_observations_with_unmatched ──► observations ──► [本仓] Evidence API
                   │                                                                │
                   └─ 读不懂的行在这里被挑走，记为 unknown_metric_code               └─ 只收到可解析的观测
```

- **本仓（genesis-evidence）**只收到能解析的观测。它自己的 `skipped` 里
  **永远不会有** `unknown_metric_code`：API 对未知的 `metric_code` 直接 400
  （实测：`{"detail":"unknown metric_code: c_reactive_protein"}`），而适配器在
  发请求**之前**就把读不懂的行挑走了。
- **health-flow** 是名字解析发生的地方，也是读不懂的行变成
  `unknown_metric_code` 的地方。这些行留在 `build_observations_with_unmatched`
  返回值里，**不跨服务边界**。（**订正 2026-10-09**：本仓适配器原先把同一件事记为
  `unknown_metric`，而读取方只认 `unknown_metric_code`，两者是不同的拼写。现已收敛为
  `core.disposition.UNKNOWN_METRIC` 一个名字，见 #261。）

## 二、本仓能做到的与本仓做不到的

| | 归属 |
| --- | --- |
| 未覆盖 rider 的**措辞与计数逻辑**（含与 `unmatched` 的分工） | **本仓**。已完成：`patient_reply_v3` / `_v2_patient_reply`，`uncovered > 0` 时在每条分支追加一句 |
| 未覆盖行的**识别**（哪些报告项名读不懂） | **health-flow**。已实现（`metric_code_for_name` 返回 `None` 即读不懂） |
| 未覆盖**条数进入患者 response** | **health-flow**。`app/api/report.py::_assess_report` 用 `local_skipped` 只补进 `typed_result.skipped`，**从不调用**本仓的 `patient_reply_v3`（实测），summary 由它自己拼 |

## 三、所以现在的实际状态

- 本仓内**没有** `unknown_metric_code` 能进入 `patient_reply` 的路径。本仓的
  rider 是**就绪但未接线**：health-flow 一旦把它的 `local_skipped` 交给
  `patient_reply_v3`（本仓已导出、可复用），提示立刻生效。
- 患者侧**目前仍看不到**未覆盖条数。这一点必须如实说，不能因为"本仓有测试"
  就当已经解决。
- **改 health-flow 不在本片范围**（另一个仓库、另一套交付流程）。它需要一个
  独立切片，且应连同「未覆盖集合是否仅算异常项」一起决定（见下）。

## 四、一个未决的口径问题

`uncovered` 现在含**所有**读不懂的行——包括参考范围内、以及没有可用数值的行。
原因是适配器在读到值与参考区间**之前**就按名字判定：名字读不懂，后面的值与区间
根本不会被解析。

因此「N 项异常不在解读范围内」这句话**不够准**：N 里混着本可能正常的行。
本仓的措辞已相应下调为「**报告另有 N 项不在当前解读范围内**」——不加「异常」，
不把未评估的说成异常。

要精确到「异常且读不懂」，必须让适配器先读值再定名，那是**改判定方式**而不是
改措辞，属另一个切片。在此之前，任何在下游把它称作「异常条数」的文案都是
在替服务下一个它没做过的判断。

## 五、给后续切片的入口

1. **health-flow 侧**：把 `local_skipped`（或其中 `unknown_metric_code` 的部分）
   传入本仓的 `patient_reply_v3`，或按同一措辞自行拼装；两者取一，不要各写一套。
2. **口径**：决定未覆盖计数是否只算异常项。若决定只算，需要先在 health-flow 的
   适配器里补值/区间判定。
3. **本仓**已就绪的部分（#261 已收敛为单一 owner）：`patient_reply_v3` 的 rider、
   计数谓词（`core.disposition.unreadable_count`）、空结果措辞的**分类**判据
   （`empty_summary`，正常/无法使用/未适用三类），以及 `patient_reply_v2` 现在也能
   承载同一句 rider，都有测试钉住。
