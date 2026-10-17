---
title: evidence-gate
desc: 一条结论进入患者侧必须连续通过的四层检查
hue: 280
---
# evidence-gate

知识卡从 `draft` 到 `published` 要连续通过四层检查；任何一层不满足都不会被绕过。

## 四层

1. **Schema** —— `published` 必须带 grade / reviewer / reviewed_at / evidence_profile_id / published_at / 非空正文。
   见 [[schema]]。
2. **主题闭合** —— 主题已 locked；每个检索流有 completed run；无 running run；每条记录有筛选决定；
   纳入项有全文结果；全文纳入项完成抽取、准入、Claim 审核。见 [[review-store]]。
3. **单条 Claim** —— Claim 审核通过；完整性 `clear`、发表状态 `formal`、已内部准入、DOI 非空、全文已处理；
   `evidence_text` / `locator` 非空；类型为 `intervention_effect`；排除动物/体外/病例系列。见 [[review-store]]、
   [[publication-integrity]]、[[methodology]]。
4. **患者文案** —— 正文通过禁用词闸门。见 [[patient-copy]]。

## 为什么闸门必须集中描述

四层分别住在 schema、存储层、审核服务与文案校验里，各自的实现者不会自然知道另外三层存在。
把它们写成同一个节点的四条不变量，是为了让"我要放宽这一条"变成一个需要修改本节点的动作，
而不是某处一个更容易通过的 if。

## 不变量

- 发布裁判只有一处：[[review-store]] 的 `_require_publishable`，人工与自主路径共用。
- 发布与**能力**是两个独立决定：已发布不等于可以给行动建议。见 [[contracts]]。
- 论文被重新评估 → 已发布卡片转 `stale` → 从患者接口消失。这是闸门的回溯生效方式。
