# 证据接口与审核工作台 QA Plan

关联 PRD：#103、#159。本计划在 T0 冻结验收契约；T6 执行全部用例，并记录提交/构建、环境、时间戳与日志/工件。每条用例必须包含 ID、环境、前置、数据、动作、可观察结果与清理。

**商品相关用例已随 ADR 0006 退役**：证据接口不再返回商品建议，患者侧商品由商城按标签映射提供。原 `QA-PUB-001`、`QA-EXCL-002`、`QA-URG-003`、`QA-EMPTY-005`、`QA-E2E-001` ~ `QA-E2E-003` 及其执行记录一并删除，不保留为「已跳过」——它们断言的行为已不存在。

## 环境基线

- Python/uv 与 `uv sync --extra dev` 完成的开发环境。
- 测试库为临时 SQLite 文件；产品或审核相关测试不得污染 `var/genesis-evidence.sqlite3`。
- 端到端使用本地服务：Evidence API（默认 `127.0.0.1:8091`）与 Review API（默认 `127.0.0.1:8090`），密钥分别通过 `GENESIS_EVIDENCE_API_KEY` 与 `GENESIS_EVIDENCE_REVIEW_API_KEY` 注入，审核员标识通过 `GENESIS_EVIDENCE_REVIEWER_ID` 注入。

## 用例摘要

| ID | 场景 | 类型 | 自动化 |
| --- | --- | --- | --- |

## 用例

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

AFK-B10：已通过，时间 `2026-08-30T03:31:15+08:00`，提交
`d13d612f1062f777310812eaf322e349dff45b0b`，Linux x86_64，Python
3.13.13、Node v24.15.0、actionlint 1.7.12、ShellCheck 0.11.0。证据：`uv
run ruff check .`、`uv run pytest`（318 passed, 1 warning）、四项质量脚本、policy
checker、actionlint、ShellCheck、`git diff --check` 全部通过；受管文件与模板逐字节一致。

AFK-B11：已通过，复用模板提交 `84e9537c661f676f68951eb3e7480472b91ff728` 的
`bash test/trusted-pr-delivery.sh`，覆盖 stale main、bundle 提交保留和远端竞态拒绝。

AFK-B12：待模板合并后在 Genesis Evidence 在线 self-hosted runner 执行 owner-authored
`agent:review` canary，保留 workflow URL、review、标签和清理证据后再标记通过。
workflow YAML 不适用复杂度或 mutation 工具；安全状态机由模板动态测试覆盖，本仓负责静态门和部署一致性。
