# 观察处置词表收敛为单一 owner（#261）：运行服务验收（2026-10-09）

**提交**：`refactor/observation-disposition`（基于 `1e80d87`）
**环境**：开发宿主，临时 SQLite + 临时对象存储，真实 `genesis-evidence-api`
进程（`127.0.0.1:8197`）
**驱动脚本**：`var/verify-261.py`（gitignored，属脚手架；取证产物见下）

## 结论

**通过，且修掉了一处实测可达的假保证。** 适配器（Health-Flow 侧）与读取方
（患者回复）此前用**两个拼写**指同一件事：适配器发 `unknown_metric`，读取方只认
`unknown_metric_code`，于是"读不懂的行"从未被计数、那条提示语在适配器自己的行上
**不可达**。现在两边都问 `core.disposition.is_unreadable`，实测该链路上的提示语出现。

同时 `patient_reply_v2` —— 报告路径唯一使用的那一个构造器 —— 结构上**无法**携带该
提示（它没有 `skipped` 形参）。现在它可以，且与 legacy v2 路径共用同一句文案。

## 观测到的证据

| # | 驱动作 | 观测结果 |
| --- | --- | --- |
| 1 | API 存活 | `200` |
| 2 | 全部参考范围内的报告 → `POST /api/evidence/matches` | `200`；`skipped` 仅含 `within_reference_range`；患者句 `已确认的指标均在参考范围内，没有需要提示的异常。` |
| 3 | 一条可读异常 + 一条读不懂的行，经适配器 | 适配器 `skipped` = `[{record_index: "2", reason: "unknown_metric_code"}]`；`uncovered` 计数 = **1**（旧拼写下为 0） |
| 4 | 该请求打到 v3 端点 | `200`，1 个 finding |
| 5 | 把**适配器自己的行**交给 `patient_reply_v3` | 患者句尾部出现 `报告另有 1 项不在当前解读范围内…`（`rider_present` = true）—— 这就是原先不可达的那条链路 |
| 6 | 同一请求改为 `schema_version=2` | `200`；线上 v2 句与改动前一致（`skipped` 里本就没有该 reason，见下） |
| 7 | 把适配器的行交给 `patient_reply_v2` | 出现同一句提示（原先结构上不可能）；不传 `skipped` 时保持原句，向后兼容 |

## 一处被实测拦下的契约变更

第一版把 v2 的空结果句也换成了 v3 的分类句，于是「全在参考范围内」的报告在
**v2** 上的患者句从 `当前没有发现可由已发布知识卡支持的异常指标。` 变成
`已确认的指标均在参考范围内，没有需要提示的异常。`

这是**外部响应契约的变更**，且不是本片要改的东西。用同一脚本对 `main`(`1e80d87`)
与本分支各跑一次 `POST /api/evidence/matches`（`schema_version` 3 与 2，观测为
参考范围内）拦下：

```
             v3                                        v2
main  已确认的指标均在参考范围内…           当前没有发现可由已发布知识卡支持的异常指标。
改前  已确认的指标均在参考范围内…           已确认的指标均在参考范围内…      ← 契约变更
改后  已确认的指标均在参考范围内…           当前没有发现可由已发布知识卡支持的异常指标。  ← 与 main 一致
```

已改为：v2 保留它自己的通用句（`disposition.NO_CARD_SUMMARY`），v3 使用分类句。
守卫 `test_the_v2_no_findings_sentence_is_unchanged_by_this_change` 钉住这一点。
**v2 唯一新增的是 rider**（那正是本 issue 要修的部分）。

## 边界（如实记录）

`skipped` 由**本仓 API 自己**产生时**仍然不会**含 `unknown_metric_code`——未知
`metric_code` 在 `validate_observation` 处直接 400，读不懂的行在 health-flow 侧就被
挑走、不跨服务边界。因此第 6 行显示：**只改本仓，患者侧仍看不到该条数**，除非
health-flow 把它的 `local_skipped` 传进来。这一点与
`docs/uncovered-reach-decision-2026-10-07.md` 的记载一致，本片不改那个决定，
只把「本仓已就绪的部分」收敛成一个 owner，并让第二个 v2 构造器也能承载它。

## 确定性检查

`ruff check .`、`uv run pytest`、`scripts/check_scope.py`、`scripts/check_schema.py`
（26 表）、`scripts/check_patient_copy.py`、`.sandcastle/repo-map.check.mjs`
全部通过；`core.disposition` 的 `demo()` 自检通过。

## 取证产物

- `var/verify-evidence-261/results.json` — 上表各项断言结果

产物落在 gitignored 的 `var/` 下；本文件是进入版本控制的那一份结论。
