# 产品推荐 QA Plan

关联 PRD：#103。本计划在 T0 冻结验收契约；T6 执行全部用例，并记录提交/构建、环境、时间戳与日志/工件。每条用例必须包含 ID、环境、前置、数据、动作、可观察结果与清理。

## 环境基线

- Python/uv 与 `uv sync --extra dev` 完成的开发环境。
- 测试库为临时 SQLite 文件；产品或审核相关测试不得污染 `var/genesis-evidence.sqlite3`。
- 端到端使用本地服务：Evidence API（默认 `127.0.0.1:8091`）与 Review API（默认 `127.0.0.1:8090`），密钥分别通过 `GENESIS_EVIDENCE_API_KEY` 与 `GENESIS_EVIDENCE_REVIEW_API_KEY` 注入，审核员标识通过 `GENESIS_EVIDENCE_REVIEWER_ID` 注入。

## 用例摘要

| ID | 场景 | 类型 | 自动化 |
| --- | --- | --- | --- |
| QA-PUB-001 | 已发布风险展示已发布推荐 | 单元/契约 | 是 |
| QA-EXCL-002 | 未发布或带风险标产品不出现 | 单元/契约 | 是 |
| QA-URG-003 | 紧急或高危风险项抑制推荐 | 单元/契约 | 是 |
| QA-FORBID-004 | 推荐文案不触发患者禁用词 | 确定性检查 | 是 |
| QA-EMPTY-005 | 未匹配到安全已发布产品时显示暂无推荐 | 单元/契约 | 是 |
| QA-E2E-001 | 端到端全链路患者可见推荐 | 端到端 | 是 |
| QA-E2E-002 | HealthFlow 上传到产品推荐跨系统验收 | 端到端 | 是 |

## 用例

### QA-PUB-001 已发布风险展示已发布推荐

- **ID**: `QA-PUB-001`
- **环境**: `uv run pytest` + 临时 SQLite；启用 T2 推荐引擎，产品表已由 T1 种子化。
- **前置**: 4 款批准种子池已发布；目标 condition 有已发布无风险标产品；fixtures 为用户年龄 >=40、finding 非紧急且 severity 小于 3。
- **数据**: `condition_code=COND_DYSLIPIDEMIA`；已发布产品 `郅臻堂植物甾醇咀嚼片`；一条已确认异常观测（如 `ldl_c` 高于参考上限）。
- **动作**:
  1. 分别通过 `ReportStore.assess()` 与 `EvidenceStore.match_published_cards()` 处理已确认观测。
  2. 读取 finding 的 `recommendations[]` 与 `product_status`。
  3. 检查每条推荐的必填字段与排序键。
- **可观察结果**: `recommendations[]` 非空并排序；每条推荐含产品名、对应营养素、推荐理由、安全提醒、免责声明、证据回链；`product_status=available`。
- **清理**: 删除临时 SQLite 文件与测试对象目录。

### QA-EXCL-002 未发布或带风险标产品不出现

- **ID**: `QA-EXCL-002`
- **环境**: `uv run pytest` + 临时 SQLite；推荐器启用。
- **前置**: 目标 condition 至少有一条 `published` 且无 `high_risk_marketing_claim` 的可用产品；用户年龄 >=40；finding 非紧急且 severity 小于 3。
- **数据**: 同一 condition 下构造五条记录：`blocked`、`in_review`、`published` 且带 `high_risk_marketing_claim`、`withdrawn`，以及一条 `published` 且无 `high_risk_marketing_claim` 的对照产品。
- **动作**:
  1. 确认 finding 非紧急、severity 小于 3 且用户年龄 >=40。
  2. 调用推荐器并保存返回产品集合。
  3. 与输入产品状态逐条比对。
- **可观察结果**: 返回集合只含状态为 `published` 且无 `high_risk_marketing_claim` 的产品；其余三类绝不出现。
- **清理**: 删除临时 SQLite 文件。

### QA-URG-003 紧急或高危风险项抑制推荐

- **ID**: `QA-URG-003`
- **环境**: `uv run pytest` + 临时 SQLite；推荐器启用。
- **前置**: 目标 condition 有可用已发布无风险标产品；用户年龄 >=40。
- **数据**: 构造三个 finding：`urgency=urgent`、`urgency=emergency`、`abnormality_severity=3`。
- **动作**:
  1. 对每个 finding 调用推荐器。
  2. 读取 `recommendations[]` 与患者侧 `patient_reply`。
  3. 断言不存在任何推荐，且 UI 状态不是空白或异常。
- **可观察结果**: `recommendations[]` 为空；患者侧显示「暂无推荐」；不抛错、不返回产品卡片。
- **清理**: 删除临时 SQLite 文件。

### QA-FORBID-004 推荐文案不触发患者禁用词

- **ID**: `QA-FORBID-004`
- **环境**: `scripts/check_patient_copy.py` 的扩展扫描器 + `FORBIDDEN_PATIENT_TERMS`；本地 shell/CI。
- **前置**: 患者侧推荐文案已接入确定性扫描目标目录。
- **数据**: 一条推荐文案，分别以合规文案和含禁用词文案（如 `治愈`、`处方`）测试。
- **动作**:
  1. 将合规推荐文案放入扫描目标。
  2. 运行检查，记录退出码。
  3. 替换为含任一禁用词的文案，再次运行，记录退出码。
- **可观察结果**: 合规文案通过；含禁用词时检查失败并输出命中词；CI 不通过。
- **清理**: 还原测试文件，移除临时数据。

### QA-EMPTY-005 未匹配到安全已发布产品时显示暂无推荐

- **ID**: `QA-EMPTY-005`
- **环境**: `uv run pytest` + 临时 SQLite；推荐器启用。
- **前置**: 目标 condition 没有 `published` 或可用产品有 `high_risk_marketing_claim`/未发布。
- **数据**: 以 `COND_CHRONIC_CONSTIPATION` 作为无产品映射条件。
- **动作**:
  1. 构造已确认 finding 并调用推荐器。
  2. 检查返回 finding 与患者侧文本。
- **可观察结果**: `recommendations[]` 为空；患者侧显示「暂无推荐」；`product_status=not_implemented`。
- **清理**: 删除临时 SQLite 文件。

### QA-E2E-001 端到端全链路患者可见推荐

- **ID**: `QA-E2E-001`
- **环境**: 本地 `uv run genesis-evidence-api`、`uv run genesis-evidence-review`、临时 SQLite/Object Store；注入 `GENESIS_EVIDENCE_API_KEY`、`GENESIS_EVIDENCE_REVIEW_API_KEY` 与 `GENESIS_EVIDENCE_REVIEWER_ID`；记录提交哈希、构建 ID、服务 URL 与执行时间戳。
- **前置**: T1 一次性迁移已就位；T2 双入口接线完成；T3 患者侧推荐块渲染完成；T4 轻量产品审核位可用。
- **数据**: 迁移后 43 个 `blocked` 候选 + 4 个 `published` 种子；一条目标为 `COND_DYSLIPIDEMIA` 的已确认异常观测（`observation_id`、`confirmation_status=confirmed`、`metric_code`、`value`、`unit`、`reference_low/high`、`evidence_text`、`source_file_index`、`source_page`、来源均完整）。
- **动作**:
  1. 启动 Evidence API（`GENESIS_EVIDENCE_API_KEY` 满足至少 24 字符）与 Review API（`GENESIS_EVIDENCE_REVIEW_API_KEY` 满足至少 24 字符并提供 `GENESIS_EVIDENCE_REVIEWER_ID`），等待 `/health` 正常。
  2. 运行确定性迁移/回填，确认 `blocked=43`、`published=4`，无旧仓运行期路径读取。
  3. 通过轻量产品审核位确认最小种子池发布状态与审计注记。
  4. `POST /api/evidence/matches`，发送 `schema_version=3` 的已确认异常观测。
  5. 断言返回 finding 的 `recommendations[]` 只含已发布无风险标产品，并含产品名、营养素、理由、安全提醒、免责与证据回链。
  6. 通过产品审核位下架一个已发布种子产品，重发同样请求，断言该产品从 `recommendations[]` 消失。
  7. 对返回的患者侧推荐文案运行 `FORBIDDEN_PATIENT_TERMS` 扫描，确认无禁用词。
- **可观察结果**: 端到端全链返回可用推荐；下架前后可见性变化符合门禁；患者文案通过禁词扫描；记录日志/工件含提交/构建、环境与时间戳。
- **清理**: 停止服务，删除临时 SQLite/Object Store 与测试日志目录。

### QA-E2E-002 HealthFlow 上传到产品推荐跨系统验收

- **ID**: `QA-E2E-002`
- **环境**: 隔离的 HealthFlow、Genesis Evidence API/Review API、SQLite 与本机 loopback 动态端口；使用真实报告解析模型。
- **前置**: 真实产品目录迁移完成；真实分类工作簿生成的映射草稿已通过 Review API 审核；高风险映射仍受发布门禁约束。
- **数据**: 一份 10 页真实报告作为阴性对照；一份明确标注为验收夹具的单页 LDL-C 报告作为正向样本。
- **动作**:
  1. 上传真实报告并等待解析，检查指标数、解析警告和异常判定。
  2. 上传 LDL-C 验收报告，等待真实模型解析并确认带完整原文证据的异常项。
  3. 由 HealthFlow 请求 Evidence API，检查疾病匹配、推荐状态、产品字段和未匹配列表。
- **可观察结果**: 真实报告解析 68 条指标且无警告，不产生虚假推荐；LDL-C `4.20 mmol/L` 高于 `3.40 mmol/L`，匹配 `COND_DYSLIPIDEMIA`，返回植物甾醇产品，`unmatched=[]`、`skipped=[]`。
- **清理**: 删除隔离数据库、报告文件、访问令牌与未脱敏日志，仅保留去标识化 QA 工件。

## 执行记录

- 结果工件：`artifacts/qa/product-recommendation-acceptance.md`
- 测试提交：Genesis `8ffbd124da8a572074dc92d49e04dbfeef0e0448`；HealthFlow `64fa5506d2c2ed8a54ce60173b5e2dc0e7930a68`
- 结论：`QA-PUB-001` 至 `QA-E2E-002` 全部通过；环境、时间戳、命令结果与残余风险见结果工件。
