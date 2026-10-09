# 论文发表完整性收敛为单一 owner（#264）：运行服务验收（2026-10-09）

**提交**：`refactor/publication-integrity`（基于 `1e80d87`）
**环境**：开发宿主，临时 SQLite + 临时对象存储，真实 `genesis-evidence-review`
进程（`127.0.0.1:8196`）
**驱动脚本**：`var/verify-264.py`（gitignored，属脚手架；取证产物见下）

## 结论

**通过。** 两篇只差 `integrity_status` 的论文（都经完整主题筛选、都可进入自动审核）
在运行服务上的可观察行为，与重构前一致：`clear` 那篇正常准入，`retracted` 那篇被
拒绝、队列状态为 `blocked`、工作台的完整性检查项为 `blocked`。准入规则现在由
`core.publication_integrity` 一处持有，七处 SQL 闸门与队列 `CASE` 都由
`integrity_sql()` 渲染，三处 store 写路径都问 `forbids_durable_evidence()`。

## 观测到的证据

| # | 驱动作 | 观测结果 |
| --- | --- | --- |
| 1 | `GET /api/review/me` | `200`（密钥匹配，排除"应用坏了"的假象） |
| 2 | `GET /api/review/papers` | `200`；`clear` 篇 `review_state=ready_for_automation`，`retracted` 篇 `blocked` |
| 3 | 队列回读的 `integrity_status` | 分别是 `clear` / `retracted`（两篇的差异**只**在这一列） |
| 4 | `POST /api/review/papers/<retracted>/admit` | **`400`**，`paper integrity must be clear before internal admission` |
| 5 | `POST /api/review/papers/<clear>/admit` | **`200`**，`{"status": "internally_admitted"}`（对照组） |
| 6 | `GET /api/review/papers/<retracted>` | `review_guidance.state = blocked`；`identity_integrity` 检查项 `status = blocked` |

第 4/5 行是同一份请求体、同一套前置、只换论文身份——因此它测的是状态，不是某条
路径恰好坏了。队列 `blocked`（第 2 行）来自 `list_review_queue` 里那句原先手写的
`p.integrity_status <> 'clear'`，现在由 `integrity_sql("p", admits=False)` 渲染。

## 三处 store 写路径的拒绝

`save_full_text`、`enqueue_extraction`、`save_ai_extraction` 三处现在都问
`forbids_durable_evidence()`。`PaperStore.integrity_status` 的 docstring 曾声称
「读者问这里，新采集路径就不会忘记闸门」，但**三个 store 路径没有一个真的调过它**。
现在三处是同一谓词；错误类型仍按调用方策略区分（前两者 `PaperRetracted`，第三处
`ValueError`），这是**有意保留**的——规则共享，后果归调用方，与 `core.methodology`
记录的做法一致。单元测试 `test_a_retracted_paper_is_refused_on_every_store_write_path`
逐个路径钉住，并配了一条 `clear` 论文不被拒的对照。

## 确定性检查

`ruff check .`、`uv run pytest`、`scripts/check_scope.py`、`scripts/check_schema.py`
（26 表）、`scripts/check_patient_copy.py` 全部通过；
`core.publication_integrity` 的 `demo()` 自检通过。

## 取证产物

- `var/verify-evidence-264/results.json` — 上表各项断言结果

产物落在 gitignored 的 `var/` 下；本文件是进入版本控制的那一份结论。
