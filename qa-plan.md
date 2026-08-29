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
| QA-E2E-003 | 真实五图体检报告上传到产品推荐 | 端到端 | 是 |

## 用例

### QA-PUB-001 已发布风险展示已发布推荐

- **ID**: `QA-PUB-001`
- **环境**: `uv run pytest` + 临时 SQLite；启用 T2 推荐引擎，产品表已由 T1 种子化。
- **前置**: 10 款批准种子池已发布；目标 condition 有已发布无风险标产品；fixtures 为用户年龄 >=40、finding 非紧急且 severity 小于 3。
- **数据**: `condition_code=COND_DYSLIPIDEMIA`；已发布产品 `郅臻堂植物甾醇咀嚼片`；一条已确认异常观测（如 `ldl_c` 高于参考上限）。
- **动作**:
  1. 分别通过 `ReportStore.assess()` 与 `EvidenceStore.match_published_cards()` 处理已确认观测。
  2. 读取 finding 的 `recommendations[]` 与 `product_status`。
  3. 检查每条推荐的必填字段与排序键。
- **可观察结果**: `recommendations[]` 非空并排序；每条推荐含产品名、对应营养素、推荐理由、安全提醒、免责声明、证据回链；`product_status=available`。
- **图片门禁**: 10 个已发布 PDF 产品逐项映射到唯一 `/products/` 图片 URL；其余 33 个候选仍为 blocked，不进入患者推荐。
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
- **数据**: 迁移后 43 个 `blocked` 候选 + 10 个 `published` 种子；一条目标为 `COND_DYSLIPIDEMIA` 的已确认异常观测（`observation_id`、`confirmation_status=confirmed`、`metric_code`、`value`、`unit`、`reference_low/high`、`evidence_text`、`source_file_index`、`source_page`、来源均完整）。
- **动作**:
  1. 启动 Evidence API（`GENESIS_EVIDENCE_API_KEY` 满足至少 24 字符）与 Review API（`GENESIS_EVIDENCE_REVIEW_API_KEY` 满足至少 24 字符并提供 `GENESIS_EVIDENCE_REVIEWER_ID`），等待 `/health` 正常。
  2. 运行确定性迁移/回填，确认 `blocked=43`、`published=10`，无旧仓运行期路径读取。
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

### QA-E2E-003 真实五图体检报告上传到产品推荐

- **ID**: `QA-E2E-003`
- **环境**: canonical HealthFlow、Genesis Evidence API 与 Review API user services；真实报告解析模型；浏览器或 API 客户端通过 loopback 服务访问。
- **前置**: 产品目录迁移和已批准种子池已就位；五张真实 JPEG 位于本机 `体检报告/` 目录；测试账号使用专用临时身份。
- **数据**: 五张真实体检报告图片；目录内 0 字节 PDF 不作为上传数据。
- **动作**:
  1. 使用临时账号上传五张图片，轮询报告状态至 `pending_confirmation`。
  2. 检查解析指标总数、解析警告、异常标记和每项来源证据。
  3. 确认全部解析指标，提交评估并读取患者侧结果。
  4. 核对疾病匹配、推荐产品、`product_status`、`unmatched` 与显式 `skipped` 结果。
- **可观察结果**: 五个文件完成解析，共 64 项指标且无解析警告；总胆固醇、LDL-C、Non-HDL 三项异常归并为 `COND_DYSLIPIDEMIA`（血脂异常）；返回已发布的「郅臻堂®植物甾醇咀嚼片」、`product_status=available`、`unmatched=[]`；正常或缺少可行动证据的其余指标进入 `skipped`，不得产生额外疾病或推荐。
- **清理**: 人工验收完成后删除临时账号、报告记录、对象文件、访问令牌和未脱敏日志；仅保留去标识化 QA 工件。

### QA-DISEASE-001 审核工作台疾病知识库按疾病聚合覆盖

- **ID**: `QA-DISEASE-001`
- **环境**: 本地 `uv run genesis-evidence-api`（Review API）+ 临时 SQLite；注入 `GENESIS_EVIDENCE_REVIEW_API_KEY` 与 `GENESIS_EVIDENCE_REVIEWER_ID`；浏览器或 HTTP 客户端经 loopback 访问工作台。
- **前置**: 至少存在 1 篇 `internally_admitted` 论文,其 `paper_admissions.condition_codes_json` 含 `COND_VITAMIN_D_DEFICIENCY`,且该论文经单一 `study_publications`→`studies`(status `verified`)关联研究设计。
- **数据**: 测试库含 39 篇 admitted 论文（按疾病分布固定）或至少 1 篇目标疾病论文；工作台已连接。
- **动作**:
  1. 打开审核工作台首页并切换「疾病知识库」页签。
  2. 读取 `GET /api/review/disease-papers` 响应,逐疾病核对 `paper_count`、`study_designs` 与 `papers[]`。
  3. 确认疾病卡显示疾病名、已收录论文数与研究设计标签（取 Top 4）。
  4. 点开目标疾病卡,展开论文清单并核对标题、年份、DOI 与研究设计。
  5. 使用搜索框按疾病名称/代码过滤。
- **可观察结果**: 每个疾病一行,`paper_count` 与 underlying admitted 论文一致,`study_designs` 分布来自 `studies.study_design`;疾病卡与论文清单按预期展开;搜索命中对应卡片;未匹配疾病不出现。仅只读聚合,不触发任何写路径。
- **清理**: 停止服务,删除临时 SQLite 与测试日志;不改动正式库。

### QA-WORKBENCH-001 审核工作台双区域滚动与可扫描布局

- **ID**: `QA-WORKBENCH-001`
- **环境**: 本地 Review API user service 或部署后的 Review API；Chrome/Chromium；审核密钥；含长队列和长审核正文的测试数据库。
- **前置**: 工作台可认证；至少存在 2 篇论文，选中论文包含步骤 1–5 的审核内容；页面构建来自待验收提交。
- **数据**: 两篇长标题论文和一篇含长摘要、步骤 1–5 内容的已选论文；其中一篇标题含 HTML 实体。
- **动作**:
  1. 以 1440px 宽、900px 高桌面视口打开工作台并认证。
  2. 记录左侧队列的 `scrollTop`，滚动右侧审核区域，再次记录左侧 `scrollTop`。
  3. 单独滚动左侧队列，确认其他论文可见并切换论文。
  4. 以 390px 宽移动视口重新打开页面，检查文档宽度与横向滚动位置。
  5. 打开一篇标题含 HTML 实体的论文，核对队列、摘要和疾病库中的标题文本。
- **可观察结果**: 桌面右侧滚动不改变左侧队列位置，左侧可独立滚动；当前论文身份和第一个待处理步骤默认展开，其余步骤可手动展开；移动端无横向滚动并按单列阅读；标题不显示 `&lt;...&gt;`、`&lt;i&gt;` 或 `&lt;sub&gt;` 等实体/标签文本。
- **清理**: 关闭浏览器并删除临时截图、临时服务和测试数据库；不改动正式审核数据。

## 执行记录

- 结果工件：`artifacts/qa/product-recommendation-acceptance.md`
- 测试提交：Genesis `8ffbd124da8a572074dc92d49e04dbfeef0e0448`；HealthFlow `64fa5506d2c2ed8a54ce60173b5e2dc0e7930a68`
- 结论：`QA-PUB-001` 至 `QA-E2E-003` 的自动化/API 验收通过；`QA-E2E-003` 的浏览器人工验收在部署后执行。环境、时间戳、命令结果与残余风险见结果工件。

## AFK 可信交付回归

### AFK-B10 可信工作流静态门

- 环境：Genesis Evidence AFK 任务分支。
- 前置：模板 1.1.1 已部署。
- 数据：六条 AFK 变更 workflow。
- 动作：运行 `node .sandcastle/policy-check.mjs workflows`、actionlint、ShellCheck，并与 afk-bootstrap 的受管文件逐字节比较。
- 可观察结果：同仓库 owner gate、可信 controller、候选只读 token、干净 delivery checkout 和 AGENT_PAT fail-closed 全部通过，受管文件无漂移。
- 清理：无。

### AFK-B11 Bundle 状态机回归

- 环境：afk-bootstrap 临时 Git 仓库测试。
- 前置：模板测试 checkout 可用。
- 数据：落后的本地 main、前进的 origin main、合并结果和远端竞态。
- 动作：运行 afk-bootstrap 的 `test/trusted-pr-delivery.sh`。
- 可观察结果：基线被重置、bundle 原样保留提交、远端竞态被拒绝。
- 清理：测试 trap 删除临时仓库。

### AFK-B12 Live canary

- 环境：Genesis Evidence self-hosted runner。
- 前置：加固 workflow 已合并，runner 与只读 token、AGENT_PAT 在线。
- 数据：仓库所有者创建的一次性 PR。
- 动作：添加 `agent:review` 并检查 workflow、review、标签和交付分支。
- 可观察结果：使用当前 main，通过 controller/candidate/delivery 隔离完成审核且没有 blocked 标签。
- 清理：关闭一次性 PR，删除临时分支和标签。

AFK-B10/B11 在合并前记录提交、环境、时间戳和命令证据；AFK-B12 在模板合并后执行。workflow YAML 不适用复杂度或 mutation 工具；安全状态机由模板动态测试覆盖，本仓负责静态门和部署一致性。
