Feature: 已确认健康风险 → 只返回已发布证据，不返回商品

  作为体检报告用户，我希望在每条已确认健康风险提示下看到有已发布知识卡支撑的证据；
  作为审核员，我希望只有经审核发布的知识卡进入患者侧。
  响应必须是证据说明，不得呈现为诊断、处方或治疗指令，**也不得包含任何商品**——
  商品权威已移到商城（ADR 0006），由商城按标签映射另行提供。

  Background:
    Given 系统已初始化「体检报告解读与健康风险提示」
    And 证据接口为 `POST /api/evidence/matches`

  Scenario: 已确认风险返回已发布证据且不含商品
    Given 已确认 finding `COND_DYSLIPIDEMIA` 的 urgency 为 `routine`
    And 该 condition 在对应 metric scope 上存在已发布知识卡
    When health-flow 提交已确认观测到 `POST /api/evidence/matches`
    Then finding 含 `condition_code`、`evidence_items` 与卡片证据回链
    And 响应中不出现 `recommendations`、`recommendation_message` 或 `product_status`
    And 响应文本不出现「商品」「产品推荐」「购物」等词

  Scenario: 未发布知识卡不进入患者侧
    Given 某 metric 只存在 `draft`、`in_review`、`approved`、`stale` 或 `rejected` 状态的知识卡
    When health-flow 提交该 metric 的已确认异常观测
    Then 该观测进入 `unmatched`，原因为 `no_published_knowledge_card`
    And 响应不为它构造 finding

  Scenario: 未命中目录或无已发布卡时如实返回
    Given 观测的 metric 不在 canonical 指标目录内，或其 condition 没有已发布知识卡
    When health-flow 提交该观测
    Then 该观测进入 `unmatched` 或 `skipped`，并给出具体 `reason`
    And 响应不构造空 finding，也不以草稿内容补充

  Scenario: 患者文案不触发禁用词
    Given 证据响应含患者可见正文与行动提示
    When CI 对患者侧文案运行 `FORBIDDEN_PATIENT_TERMS` 确定性扫描
    Then 文案不包含「诊断、确诊、处方、治愈、根治、排毒、抗癌、逆龄」中的任一词语
    And 若包含任一禁用词，CI 检查失败

  Rule: 审核工作台按疾病聚合已收录论文覆盖

  Scenario: 疾病知识库页签按疾病显示已收录论文数与研究设计分布
    Given 审核员已认证并打开「疾病知识库」页签
    And 存在 `internally_admitted` 论文,其 `condition_codes_json` 内含 `COND_VITAMIN_D_DEFICIENCY`
    When 工作台请求 `GET /api/review/disease-papers`
    Then 每行按 `condition_code` 聚合,包含 `paper_count`、`study_designs` 与 `papers[]`
    And 疾病卡显示疾病名称、已收录论文数与研究设计标签
    And 点开卡片可展开该疾病下的论文清单（标题、年份、DOI 与研究设计）

  Rule: 审核工作台支持独立阅读与可扫描的步骤导航

  Scenario: 桌面端论文队列与审核内容独立滚动
    Given 审核员已认证并打开一篇包含长审核内容的论文
    When 审核员在 1440px 宽桌面滚动右侧审核内容
    Then 左侧论文队列位置保持不变
    And 左侧论文队列可单独滚动查看其他论文
    And 当前论文身份、审核状态与第一个待处理步骤清晰可见

  Scenario: 移动端工作台恢复单列阅读且标题可读
    Given 审核员使用 390px 宽移动视口打开工作台
    When 页面加载包含长英文标题与审核步骤的论文
    Then 页面不产生横向滚动
    And 论文队列、论文摘要与审核步骤按单列顺序阅读
    And 标题不显示 `&lt;...&gt;` 或其他 HTML 实体标记

  Rule: AFK 拉取请求自动化使用可信控制面

  Scenario: 持久化 runner 使用当前 main 作为审核基线
    Given runner 的本地 main 已落后于 origin main
    When 仓库所有者创建的同仓库 PR 触发 agent:review
    Then 可信 controller 将本地 main 重置到当前 origin main
    And 审核差异以该当前基线计算

  Scenario: 候选代码不能获得交付凭据
    Given 仓库所有者创建的同仓库 PR 触发 AFK 变更工作流
    When 工作流执行候选分支
    Then 宿主依赖和编排只从 main controller 加载
    And 候选命令只在带只读 token 的 Docker 沙箱执行
    And 干净 delivery checkout 导入并推送已验证的 Git bundle

  Scenario: 不可信 PR 或缺失交付凭据时停止
    Given PR 来自 fork、作者不是仓库所有者，或 AGENT_PAT 不可用
    When PR 被添加 AFK 变更标签
    Then 工作流不报告成功交付
    And 凭据失败时记录 agent:blocked
