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
