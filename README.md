# Genesis Evidence

面向成人 40+ 人群的医学论文证据生产、个人体检报告解读与健康管理建议系统。

**产品对外名称**：体检报告解读与健康风险提示。本仓库是该产品的证据侧运行时；报告上传与
多模态解析由并行的 `health-flow` 服务提供，两者只通过 HTTP/JSON 契约相连。

---

## 1. 第一性原理

把"体检报告 → 健康建议"做成可审计的工程系统，必须先回答一个问题：**系统允许自己说什么？**
本仓库的全部设计都可以从这一条约束推导出来。

1. **系统只复述已发布的、逐条可追溯的内容，不生成新的医学结论。**
   运行时不调用 LLM 做疾病判断、剂量或治疗建议。患者看到的每一句话都来自一条
   `published` 知识卡，而知识卡正文由人在审核工作台确认后写入。运行时的语言模型只允许
   出现在**录入侧**（报告 OCR/结构化、论文事实抽取），其输出一律是待人工确认的候选数据。
2. **模型输出是候选，不是事实。** 论文抽取跑两次独立抽取再做一致性检查，冲突必须裁决；
   报告抽取的每个数值必须能在原文中找到字面证据，否则该行被标记校验问题、不得直接进入匹配；
   未确认的观测永远不参与匹配。
3. **证伪优先于进度。** 证据闸门（第 5 节）宁可拒绝发布，也不补一条证据、不降低一个阈值。
   疾病覆盖矩阵在筛选台账未闭合时保持 `screening`，而不是提前显示 `claims_ready`
   或 `full_text_ready`。
4. **两条主线只在一个点耦合。** 论文证据线与报告解读线不共享模型、不共享提示词、不共享运行
   进程；它们唯一的连接面是 `knowledge_cards.status = 'published'`。这条约束让"哪些内容可以
   到达患者"成为一个可以被 SQL 检查的问题，而不是一个需要阅读全部代码才能回答的问题。
5. **审计即证据。** 每次状态迁移、准入、拒绝、发布、重试都写入 `audit_events`，并带
   `actor`（人工审核员来自服务端环境变量，不是浏览器参数）。敏感标识不进审计：
   报告接口只记录计数与代码，不记录图像与患者标识。

由第 1 条推导出的可执行推论：

- 患者可见文案由 `core/patient_copy.py` 的禁用词表在**写入时**与**CI 时**双重拦截；
- 知识卡的 `published` 状态由 `CHECK` 约束保证字段完整（grade / reviewer / reviewed_at /
  evidence_profile_id / published_at / 非空正文），不可能出现"半发布"；
- 卡片发布还要过一次证据闸门（`_require_publishable`），它是"这条结论站不站得住"的唯一裁判。

---

## 2. 系统全景

### 2.1 主线 A：论文证据生产（可自主执行，人工处理例外）

```text
evidence_topics (locked)
        │  required_search_streams × 版本化检索式
        ▼
collection_runs ──► collection_papers ──► full_texts
  连接器          │ 题录筛选            │ 全文检索状态
  DOAJ/EPMC/CORE  │ 全文筛选            │
                  ▼                     ▼
              paper_extraction_jobs (extraction_a → extraction_b → consistency → saved)
                  │  两次独立同模型抽取 + 一致性检查
                  ▼
              paper_admissions ──► claims ──► claim_reviews
                  │  研究身份/设计/注册号         │ 逐条 Claim 审核
                  ▼                               ▼
              studies / study_publications   evidence_profiles (evidence_body_complete=1)
                                                  │  按 PICOTS 结果范围建档 + GRADE 确定性
                                                  ▼
                                             knowledge_cards ──► card_claims
                                                  │  draft→in_review→approved→published
                                                  │  ← 证据闸门在此判定
                                                  ▼
                                          【唯一对患者可见的产物】
```

图中的"← 证据闸门在此判定"指卡片发布前必须通过的闸门：schema 完整性、主题台账闭合、
每条 Claim 可追溯且无高危偏倚、患者文案通过禁用词检查。逐层检查项见第 5 节。

人工审核工作台（`review/api.py` + `workbench.html`）把这条链路切成 5 个可折叠步骤，每步都是
"AI 先执行，人工可覆盖"：① 主题圈定与筛选 → ② 两次抽取差异裁决 → ③ 身份确认与论文准入 →
④ Result 与 Claim 审核 → ⑤ Evidence Profile 与知识卡。AI 的自动裁决写在审计里，人工覆盖会
另写一条审计。

### 2.2 主线 B：个人报告解读

```text
health-flow：多文件上传 → 逐页解析（页码/BBox）→ 用户确认观测
        │  POST /api/evidence/matches   （schema_version 3，X-Genesis-Evidence-Key）
        ▼
portal/api.py ──► EvidenceStore.match_published_cards
        │  ① 校验：metric_code 在 canonical 目录；数值与每个参考界必须字面出现在 evidence_text
        │  ② 确定性判异：用确认后的数值与参考界重算，不信任上游的 abnormal_flag
        │  ③ metric_code → conditions 目录 → 该 condition 的 published 卡
        │  ④ 按 condition 聚合 finding，每个 metric 保留自己的证据项与卡片等级
        ▼
患者可见响应：findings[] / unmatched[] / skipped[] + 产品推荐（若已审核发布）+ 审计事件
```

隔离约束：报告接口是**只读**的，它不拥有上传、不拥有抽取 worker。两条主线之间不共享
SQLite 文件、ORM 模型、向量索引或提示词。

> **仓库内状态说明**：`core/store/reports.py` 与 `reports/` 保留了第一阶段"单仓库自持报告"的
> 上传 → 确认 → 评估链路，它仍然是 `EvidenceMatcher` 三个入口之一，也是论文证据侧
> `ConditionDefinition` 与患者文案的来源。但**当前线上没有进程运行它**：报告上传与解析已由
> health-flow 的 `health-flow-report-worker.service` 承担。这部分代码是活契约、无运行时。

### 2.3 服务与端口

| 服务 | 入口 | 端口 | 现状 |
| --- | --- | --- | --- |
| Evidence API（只读证据查询） | `genesis_evidence.portal.api` | 127.0.0.1:8125 | 运行中；仅内网，由 health-flow 调用 |
| 审核工作台 | `genesis_evidence.review.api` | 127.0.0.1:8126 | 运行中；`genesis-evidence-review.ranlei.work` |
| 论文抽取 worker | `genesis_evidence.literature.extraction_worker` | — | **已停止（disabled）**：单主题低速验收暂停，不领取任务 |
| 个人报告门户（用户端） | health-flow | 127.0.0.1:8127 | 运行中；`genesis-evidence.ranlei.work` |
| 报告解析 worker | health-flow | — | 运行中 |

部署拓扑、环境变量与旧 `genesis-health` 域名的区分见 [docs/deployment.md](docs/deployment.md)。

---

## 3. 模块地图：定义与依赖方向

依赖方向自上而下，**下层不得反向依赖上层**。包围号内是当前包的真实文件行数。

```text
                    ┌─────────────────────────────────────────┐
   CLI / 进程入口 →  │ portal(140)  review(462+html)  literature│
                    │ integrations(175)      .extraction_worker│
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
  应用/编排层      →  │ review.service(1355)  review.scope(729)   │
                    │ literature.{ingestion,ai_extraction,...}  │
                    │ products.catalog(727)  reports.extraction │
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
  持久化层         →  │ core.store.{papers,review,evidence,       │
                    │              reports,database}(6.4k 行)   │
                    └───────────────┬─────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────┐
  确定性内核（纯） →  │ core.{matching,conditions,metrics,        │
                    │       contracts,patient_copy}             │
                    │ products.recommendations(333)             │
                    └─────────────────────────────────────────┘
```

### 模块定义

| 模块 | 一句话定义 | 关键不变量 |
| --- | --- | --- |
| `core.matching` | 确定性"已确认观测 → 已发布卡片"链条的深模块：统一登记表、参考界闸门、scope 解析器与三条投影缝 | 三条入口（`EvidenceStore.match_published_cards` / `ReportStore.match_published_cards` / `ReportStore.assess`）只是薄调用者，只差异于卡片来源、scope 严格度、结果投影 |
| `core.conditions` | 首批 12 个健康问题目录（`condition_code → 指标集合 + 科室 + 复查方向`） | 运行时会随 `Database.initialize()` upsert 进 `conditions` 表，是唯一可写的目录来源 |
| `core.metrics` | 30 个 canonical `metric_code` 与中文标签、别名归一化、字面数值匹配 | `evidence_contains_value` 是"证据必须字面出现"的判定本身 |
| `core.contracts` | v2/v3 请求响应模型与能力分层（`card_capabilities`） | 请求 `schema_version` 只接受 `"2"` / `"3"`；v2 保持扁平旧形状以便独立切换 |
| `core.patient_copy` | 患者可见文案禁用词闸门 | 同一表同时被写入路径与 CI 使用，两者不可能漂移 |
| `core.store.database` | `BEGIN IMMEDIATE` 单写事务边界 + schema 初始化 + 幂等迁移 | 队列领取、状态迁移都在同一立即写事务内完成 |
| `core.store.papers` | 论文域持久化（1600+ 行）：采集、全文对象、抽取任务、准入、Claim、Result | 任务领取依赖 `paper_extraction_jobs_active_paper_unique` 部分唯一索引：同一论文最多一个 queued/running |
| `core.store.review` | 审核域持久化（2300+ 行）：主题台账、覆盖矩阵、知识卡与**证据闸门** | `_require_publishable` 是唯一的发布裁判；`evidence_body_complete=1` 由系统判定，不接受人工置位 |
| `core.store.evidence` | 对外只读匹配：只读已发布卡片与已发布产品，写审计 | 只有 `status='published' AND grade IN (high,moderate,low)` 的卡可见 |
| `core.store.reports` | 报告域持久化：上传、确认、评估（`assess`）、无状态复算（`match_published_cards`） | 只有 `confirmed` 的报告可被评估；确认后的观测才进入匹配 |
| `literature.*` | 检索连接器（DOAJ/Europe PMC/CORE）、全文下载与版权策略、JATS 解析、完整性（撤稿/更正）核查、Ark 抽取与一致性检查 | 连接器只发元数据；CORE 在许可证确认前不可下载；Sci-Hub 类 URL 直接拒绝 |
| `review.service` | 自主论文审核编排：筛选台账终结、身份准入、自动 Claim 审核、自动建档、卡草稿与迁移 | 每个自动决定都写 `policy_version`（当前 `literature-review-ai/1.5`）与 `requested_by` |
| `review.scope` | PICOTS 匹配与 Evidence Profile 稳定结果范围解析（`metric:<code>` / `condition:<code>` / `outcome:<slug>`） | 无状态、无存储依赖；**禁止** import `genesis_evidence.review`（会成环） |
| `products.catalog` | 产品候选层（`blocked` 池）与已发布推荐池的审核、迁移、发布闸门 | 带 `high_risk_marketing_claim` 的产品永远不能进入 `published` |
| `products.recommendations` | 确定性纯函数 `recommend(...)` + 已发布产品加载 | 与疾病匹配解耦：condition_code 由调用方给出，引擎不做二次疾病匹配 |
| `reports.*` | 有序多模态报告抽取（停在用户确认之前）、抽取评估指标、持久化 worker | 抽取不产生诊断，只产生候选观测与原文证据 |
| `integrations.health_flow` | health-flow `MetricRecord` → Evidence API v3 的确定性适配器 | 必须显式传入 `confirmed=True`，未确认行只能进 `skipped` |
| `portal.api` | 唯一对外证据接口：鉴权、CSP/无缓存头、UUID 关联 ID、审计 | 不信任上游 `abnormal_flag`、VLM 置信度与 RAG 结论 |

### 依赖方向（实测）

**无环的骨架**（`A → B` 表示 A import B）：

```text
core.conditions / core.metrics   ←  所有模块
core.matching                    ←  core.store.{evidence,reports}, portal, 测试
products.recommendations         ←  core.store.{evidence,reports}, products.catalog
integrations.health_flow         ←  portal, tests
```

**已知的回边（需要解释，不是巧合）**：

| 回边 | 原因 |
| --- | --- |
| `core.store → literature.{ai_extraction, models}` | 存储层写入前需要 `CheckedPaperExtraction` 与 `PaperRecord` 类型校验，避免"先落库再校验" |
| `core.store → products.recommendations` | 患者响应在 finding 装配点直接附加推荐，而不是二次请求（见 ADR 0003） |
| `core.store → reports.extraction` | 报告类型与确认输入模型被存储层直接复用 |
| `review.service → literature.{ai_extraction, jats}` | 自动审核需要研究设计枚举与 JATS 事实抽取 |
| `literature.extraction_worker → review.service` | worker 在每个阶段结束后调用自动审核 |
| `core.store → review.scope` | Evidence Profile scope 解析是存储层建档的唯一来源 |

`review/scope.py` 的 docstring 明确记录了一条约束：它不能 import `genesis_evidence.review`
包，因为该包 `__init__` 会拉起 `review.service`，进而拉起 `core.store`，与 `core.store → review.scope`
构成环。**改动 `review/scope.py` 的 import 前先读这段注释。**

数据层还保留两条兼容缝，属已知迁移债：`database._migrate_existing_schema` 把历史共享的
`results` 表按 claim 拆分；`reports/extraction.py` 重新导出 `evidence_contains_value` 供旧调用者
（`reports/evaluation.py`）使用——但注册的 `genesis-evidence-evaluate` 入口点并不在
`pyproject.toml` 的 `[project.scripts]` 里，直接运行会 `ModuleNotFoundError`，应使用
`python -m genesis_evidence.reports.evaluation`。

---

## 4. 数据模型：31 张表的三个簇

行数即复杂度预算，`Database.initialize()` 同时完成建表、幂等迁移与目录 upsert。

| 簇 | 表 | 关键约束 |
| --- | --- | --- |
| 论文证据（19） | `conditions`, `evidence_topics`, `collection_runs`, `papers`, `paper_sources`, `collection_papers`, `full_texts`, `paper_extraction_jobs`, `paper_admissions`, `studies`, `study_publications`, `paper_extractions`, `claims`, `results`, `claim_reviews`, `evidence_profiles`, `evidence_profile_results`, `knowledge_cards`, `card_claims` | `papers` 上三个部分唯一索引保证 DOI/PMID/PMCID 去重；`studies`/`study_publications` 分离"研究身份"与"报告身份"；`knowledge_cards` 的 `CHECK` 保证 `published` 必须字段齐全 |
| 报告与患者可见（6） | `reports`, `report_files`, `report_observations`, `observation_confirmations`, `assessments`, `assessment_findings` | `assessment_findings` 关联 `condition_code` 与源观测；患者响应只读这些表 + `knowledge_cards` |
| 产品目录（5） | `product_candidates`, `product_candidate_sources`, `product_recommendations`, `product_review_audits`, `product_mapping_drafts` | `product_recommendations.status='published'` 要求 `audit_note` 非空；来源记录 `source_sha256` 唯一，保证溯源不重复 |
| 横切（1） | `audit_events` | 所有状态迁移的唯一落点；`actor` 由服务端决定 |

> `scripts/check_schema.py` 强制 `表数 ≤ 31`。**新增一张表就是一次架构决定**，必须同时回答
> "它属于哪个簇、它的不变量为什么不能写在现有表上"。

表预算是这个项目最有效的反膨胀装置：它把"顺手多建一张表"从无害习惯变成必须显式审批的动作。

---

## 5. 证据闸门：一条结论必须同时满足什么

知识卡从 `draft` 走到 `published` 要连续通过四层检查，任何一层不满足都**不会**被绕过：

| 层 | 检查 | 位置 |
| --- | --- | --- |
| 1. Schema | `published` 必须带 grade / reviewer / reviewed_at / evidence_profile_id / published_at / 非空正文 | `schema.py` 的 `knowledge_cards` CHECK |
| 2. 主题闭合 | 主题已 `locked`；每个要求的检索流都有 completed run；无 running run；每条记录都有题录筛选决定；纳入项有全文检索结果；全文纳入项完成抽取、准入、Claim 审核 | `_require_complete_topic` |
| 3. 单条 Claim | Claim 审核为 `approved`；论文完整性 `clear`、发表状态 `formal`、已内部准入、DOI 非空、全文已处理；`evidence_text` / `locator` 非空；Claim 类型为 `intervention_effect`；排除动物/体外/病例系列设计 | `_require_publishable` |
| 4. 患者文案 | `patient_visible_body` 通过禁用词闸门 | `core.patient_copy` |

`_require_publishable` 还要求：至少一条 Claim、每条 Claim 在 Profile 中有对应 Result、
scope_key 非空，且**上下文卡（grade = `low`）不得携带 `high`/`critical`/`uncertain` 的偏倚风险判定**。
只要一篇论文被重新评估，其已发布卡片会被置为 `stale` 并从患者接口消失（`_stale_cards_for_paper`）。

结果层的门禁：`evidence_profiles.evidence_body_complete` 由 `CHECK (evidence_body_complete = 1)`
约束，建档时即在同一个主题闭合检查之后写入，而不是事后人工置位。

发布与**能力**是两个独立决定——`core.contracts.card_capabilities(grade)` 让任何已发布卡片
（包括 `moderate`/`high`）都返回 `content_layer=context_only`、`action_status=not_available`，
直到存在单独审核过的行动内容。已发布卡片必须使用 `研究提示` 这类研究语言
（PRD #103 已确认的决定），不得成为诊断、产品推荐、剂量或治疗指令。

---

## 6. 数据如何流动：三条端到端路径

**路径 1 — 论文到卡片**：`collection_runs`（一个检索流一次运行）→ `collection_papers`（同一论文可在多个
运行中出现，应用层按 DOI/PMID/PMCID 归并为一条知识）→ `full_texts`（对象存储键 + SHA-256 +
版权状态）→ `paper_extraction_jobs`（逐阶段持久化，每阶段结束是一次可重放的状态）→
`paper_admissions`（准入决定 + 一致性裁决）→ `claims` + `claim_reviews` → `evidence_profiles`
（按稳定结果范围聚合，`evidence_body_complete=1`）→ `knowledge_cards` + `card_claims`。

**路径 2 — 报告到患者响应**：health-flow 确认观测 → 每个 metric 独立走一遍
`EvidenceMatcher` → 同一 condition 的多个 metric 聚合为一个 finding（每个保留自己的 evidence item；
等级不同的 finding 报 `evidence_strength="mixed"`，绝不塌缩成单一等级）→ 未覆盖的关联进
`unmatched`，正常或证据不足的进 `skipped` → 在同一装配点附加已发布产品推荐
（抑制条件：紧急/危重 urgency、severity ≥ 3、或已知年龄 < 40）。

**路径 3 — 审核员动作**：工作台 → Bearer 鉴权 → `review/api.py` → `ReviewStore` 或
`EvidenceReviewService`（自动路径）→ 同一事务内写状态 + 写 `audit_events`。
AI 自动执行与人工覆盖产出的都是带 actor 的审计记录，事后无法区分"谁做的"这件事被当作缺陷而不是特性。

---

## 7. 为什么这样拆分

| 决策 | 依据 |
| --- | --- |
| 推荐挂在已确认 finding 下，不另做疾病匹配 | [ADR 0003](docs/adr/0003-anchor-recommendations-under-confirmed-findings.md) |
| 产品数据一次性迁移进本仓库，运行时不再读旧库 | [ADR 0002](docs/adr/0002-one-time-product-catalog-migration-and-self-governance.md) |
| 推荐从冻结的旧仓库迁回本仓库 | [ADR 0001](docs/adr/0001-unfreeze-phase-2-product-recommendations.md) |
| 只发布 4 条人工审核过的种子推荐，其余 39 条候选保持 `blocked` | [ADR 0004](docs/adr/0004-four-product-seed-pool-publication.md)（后续审核已把已发布池扩到 10 条） |
| 工作台采用独立阅读区域（队列与内容各自滚动） | [ADR 0005](docs/adr/0005-review-workbench-independent-reading-regions.md) |
| AFK 变更工作流的可信控制面 | [ADR 0002（可信 PR 控制面）](docs/adr/0002-trusted-pr-control-plane.md) |
| 每条外部标准的具体映射 | [docs/evidence-governance-standards.md](docs/evidence-governance-standards.md)（PRISMA 2020、Cochrane/MECIR、AHRQ PICOTS、RFC 6750、NISO JATS 1.3、Ark 流式协议、SQLite 事务/部分索引、systemd 重启策略） |

### 已知缺口（不要当成已完成）

- `evidence_profiles.certainty` 是**单值确定性**字段，不是逐域 GRADE 记录，尽管工作台文案写着
  "显式记录 GRADE 域"。`very_low` 不能发布（`knowledge_cards` CHECK + `transition_card` 双重拦截），
  `high`/`moderate` 要求偏倚风险判定已解决为非高危。
- `needs_recheck` 目前是固定值 `True`（`core/store/reports.py:494`），没有基于审核状态的复查策略；
  `matching.py` 里已用 `# ponytail:` 注释标明"在经审核的指标专属阈值发布前，
  偏离参考界一律记为 routine / severity 1"。
- 阶段 3 集成设计提到的上游标签映射与匹配端点共享缓存尚未实现。
- `docs/adr/0002` 的 Consequences 仍写着 `blocked=43 / published=4`；当前脚本断言的是
  `10` 条已发布推荐（候选仍是 43 条 blocked），ADR 0004 的"4 条种子"已被后续审核扩池超越。

---

## 8. 改动指南

**改"系统允许说什么"** → 先改 `acceptance.feature` 与 `qa-plan.md`（被 `tests/test_governance_artifacts.py`
强制校验结构），再改 `core/patient_copy.py`，最后改代码。验收与 QA 用例 ID 集是 CI 的一部分。

**改匹配语义** → 只动 `core/matching.py`。三条入口共享同一实现，"在哪条路径修"通常说明引入了一个
不该存在的分支。

**改发布规则** → 只动 `_require_publishable` / `_card_passes_publish_gate`。

**加表** → 同时回答第 4 节的表预算问题，并更新 `scripts/check_schema.py` 的 `MAX_TABLES`
与 `tests/test_schema.py` 的期望集合。

**改领域术语** → 只动 `CONTEXT.md`（术语表，不含实现决策），决策写进 `docs/adr/`。
`tests/test_governance_artifacts.py` 会校验术语与 ADR 结构。

**改自动化审核行为** → `review/service.py`，并提升 `AUTONOMOUS_REVIEW_POLICY_VERSION`，
否则审计无法区分新旧策略下的决定。

---

## 9. 开发

```bash
uv sync --extra dev
uv run ruff check .                                  # 静态检查
uv run pytest                                        # 318 个测试
uv run python scripts/check_scope.py                 # 冻结产品面（13 个标识符不得出现在 src）
uv run python scripts/check_schema.py                # 表数预算（31）
uv run python scripts/check_patient_copy.py          # 患者文案禁用词
uv run python scripts/check_product_catalog.py       # 43 blocked / 10 published / 0 无来源
uv run python -m genesis_evidence.reports.evaluation \
  evals/report-gold.synthetic.jsonl evals/report-predictions.synthetic.jsonl   # 抽取评估
```

复杂度与变异测试按风险触发（`pyproject.toml` 的 `[tool.mutmut]` 已限定到匹配器、目录与推荐引擎）：

```bash
uv run radon cc -s -a -n C src/genesis_evidence/core/matching.py
uv run mutmut run && uv run mutmut results
```

前四个 guard 由 `.github/workflows/quality.yml` 在 CI 中强制执行。`check_review_workbench_layout.py`
需要 headless Chrome，不在 CI 中运行，改动 `workbench.html` 的滚动边界时本地执行。

本地进程（不承载报告上传）：

```bash
GENESIS_EVIDENCE_API_KEY=...  uv run genesis-evidence-api       # 只读证据接口
GENESIS_EVIDENCE_REVIEW_API_KEY=... uv run genesis-evidence-review   # 审核工作台
PAPER_AI_API_KEY=...          uv run genesis-evidence-worker    # 论文抽取（当前线上停止）
```

`genesis-evidence-backlog`（`literature/backlog.py`）处理全文获取与抽取积压，并会重试失败的
抽取任务；它默认处理全部主题，**只有**设置了 `GENESIS_EVIDENCE_ACTIVE_TOPIC_ID`
（或旧名 `GENESIS_EVIDENCE_BACKLOG_TOPIC_ID`）才会收敛到单主题。
`genesis-evidence-worker` 与它共用 `var/review.env`；私有 env 文件不提交 Git，模板见 `ops/examples/`。

### 产品目录

产品候选与已发布推荐是分离的两层。从旧文献库一次性回填候选池：

```bash
uv run python scripts/migrate_product_catalog.py --source-db ../genesis-health/var/literature/literature.db
uv run python scripts/check_product_catalog.py
```
