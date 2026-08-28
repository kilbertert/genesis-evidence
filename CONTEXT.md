# CONTEXT.md

本文件是仓库级术语与治理契约的子集，服务于「已确认健康风险 → 可审核、可发布的营养产品推荐」功能。命名以 PRD #103 为准，后续切片不得在同义概念上另造术语。

## 产品定位

- 产品对外名称统一为 `体检报告解读与健康风险提示`。
- 患者侧出现的结论称为 `健康风险提示`，不是医疗诊断，不得使用诊断类措辞或给出处方、剂量、治疗方案。
- 推荐是 `健康管理建议`，说明可考虑的膳食/营养素方向；它不是医疗建议、诊断、处方，也不替代治疗，不与销售流程强绑定。
- 供应商原始宣称永不进入患者侧文案；患者侧只展示经审核重建的推荐卡片。

## 产品推荐核心术语

| 术语 | 约束 |
| --- | --- |
| `finding` / risk finding | 已确认异常观测经现有确定性匹配后产出的风险项，是推荐挂载的唯一锚点。 |
| `condition_code` | 复用已确认 finding 的既有条件代码（如 `COND_DYSLIPIDEMIA`），推荐不做二级疾病匹配。 |
| `recommendations[]` | finding 的伴生推荐块，由确定性纯函数 `recommend(condition_code, confirmed_observations)` 产出。 |
| `product_status` | `available` 或 `not_implemented`；存在合规推荐时可用，其余情况保持兼容语义。 |
| `blocked` | 候选层产品；从旧仓迁移后保持 blocked，推荐器永不读取该层。 |
| `published` | 经轻量产品审核位发布、无风险标的产品；只有该层可进入患者侧推荐池。 |
| `high_risk_marketing_claim` | 风险标（如溶血栓、降血糖、治愈）；带该标记的产品只进入「不可推荐/需复审」池，即使人工审核通过也不进推荐池。 |
| `产品审核位` | 轻量 CLI/接口形式的审核与发布门禁，不建厚重工作台；推荐文案与产品同一位审。 |
| 产品生命周期 | `blocked → in_review → published → withdrawn`；发布才可见，下架后患者侧推荐消失。 |
| `patient_visible_body` / `action_message` | 患者侧展示位；推荐块复用它并增加产品名、营养素、理由、安全提醒、免责与证据回链。 |

## 风控与文案边界

- 紧急或高危 finding 抑制推荐（对齐旧仓 `suppressed_for_urgent_evaluation` 语义）。
- 推荐文案必须通过 `FORBIDDEN_PATIENT_TERMS` 确定性扫描；触发 `诊断、确诊、处方、治愈、根治、排毒、抗癌、逆龄` 时 CI 失败。
- 年龄 <40 不产生推荐；本功能最小范围只面向成人 40+ 体检报告用户。

## 数据与迁移

- 产品目录从旧仓 `genesis-health/var/literature/literature.db` 的候选层一次性迁入本仓自有表；迁移后本仓自治，不再有旧仓运行期依赖。
- 迁移后 43 个候选产品保持 `blocked`；仅 PRD 已批准的 4 款最小种子池标记为 `published`。
- Excel 功能分类表只作为离线映射草拟种子，不做结构化产品目录。

## 相关决策

- `docs/adr/0001-unfreeze-phase-2-product-recommendations.md`
- `docs/adr/0002-one-time-product-catalog-migration-and-self-governance.md`
- `docs/adr/0003-anchor-recommendations-under-confirmed-findings.md`
- `docs/adr/0004-four-product-seed-pool-publication.md`
