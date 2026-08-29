Feature: 已确认健康风险 → 可审核可发布的营养产品推荐

  作为 40 岁以上体检报告用户，我希望在每条已确认健康风险提示下看到合格的健康管理建议；
  作为审核员，我希望只有经审核发布的低风险产品进入患者侧。
  推荐必须是健康管理建议，不得呈现为诊断、处方或治疗指令。

  Background:
    Given 系统已初始化「体检报告解读与健康风险提示」
    And 产品推荐已在已确认风险 finding 的装配点接入

  Scenario: 已发布风险展示已发布推荐
    Given 已确认 finding `COND_DYSLIPIDEMIA` 的 urgency 为 `routine`
    And 该 finding 的 abnormality_severity 小于 3
    And 用户年龄不小于 40
    And 已发布无风险标产品「郅臻堂植物甾醇咀嚼片」已映射到该 condition
    When 系统生成该 finding 的 `recommendations`
    Then 推荐列表非空且按证据强度与异常严重度排序
    And 每条推荐包含产品名、对应营养素、推荐理由、安全提醒、免责声明与证据回链
    And 每条已发布 PDF 产品推荐包含同源产品图片 URL
    And 该 finding 的 `product_status` 为 `available`

  Scenario: 未发布或带风险标的产品不出现
    Given 已确认 finding `COND_VITAMIN_D_DEFICIENCY` 的 urgency 为 `routine`
    And 该 finding 的 abnormality_severity 小于 3
    And 用户年龄不小于 40
    And `blocked`、`in_review`、`withdrawn` 状态或带 `high_risk_marketing_claim` 的产品可与该 finding 关联
    When 系统生成该 finding 的 `recommendations`
    Then 推荐列表只包含状态为 `published` 且无 `high_risk_marketing_claim` 的产品
    And 未发布、已下架或带风险标产品一律不出现

  Scenario: 紧急或高危风险项抑制推荐
    Given 已确认 finding `COND_DYSLIPIDEMIA` 已映射到已发布无风险标产品
    And 用户年龄不小于 40
    And 该 finding 的 urgency 为 `urgent` 或 `emergency`，或 abnormality_severity 为 3
    When 系统生成该 finding 的 `recommendations`
    Then `recommendations` 为空
    And 患者侧显示「暂无推荐」而不是空白或报错

  Scenario: 推荐文案不触发患者禁用词
    Given 推荐器返回已发布无风险标产品
    And 推荐文案写入患者侧 `patient_visible_body` / `action_message` 伴生推荐块
    When CI 对患者侧推荐文案运行 `FORBIDDEN_PATIENT_TERMS` 确定性扫描
    Then 文案不包含「诊断、确诊、处方、治愈、根治、排毒、抗癌、逆龄」中的任一词语
    And 若包含任一禁用词，CI 检查失败

  Scenario: 未匹配到安全已发布产品时显示暂无推荐
    Given 已确认 finding `COND_CHRONIC_CONSTIPATION` 没有安全已发布产品映射
    And 用户年龄不小于 40
    When 系统生成该 finding 的 `recommendations`
    Then `recommendations` 为空
    And 患者侧显示「暂无推荐」
    And 该 finding 的 `product_status` 为 `not_implemented`

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
