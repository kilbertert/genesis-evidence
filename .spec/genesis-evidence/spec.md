---
title: genesis-evidence
desc: 已确认健康观测 → 已发布证据解读；证据的每一层由确定性内核把关，AI 只提议
hue: 45
related:
  - src/genesis_evidence/__init__.py
  - src/genesis_evidence/core/__init__.py
  - src/genesis_evidence/core/store/__init__.py
  - src/genesis_evidence/integrations/__init__.py
  - src/genesis_evidence/literature/__init__.py
  - src/genesis_evidence/literature/connectors/__init__.py
  - src/genesis_evidence/portal/__init__.py
  - src/genesis_evidence/reports/__init__.py
  - src/genesis_evidence/review/__init__.py
---
# Genesis Evidence

把"已确认的健康观测"变成"患者能读、能追溯原文、且不越界成诊断"的证据解读。

## raw source

这个服务的价值不在于检索或生成，而在于**结论的可信边界**：一条结论要么同时满足全部闸门，要么不出现。
宁可少说，不可说错。患者看到的每一句话背后必须有已发布知识卡与原文回链；AI 的产出是候选，不是结论。

## expanded spec

两条生产线共用同一个证据体：

- **主线 A 论文证据生产** —— 检索 → 全文 → 抽取 → 一致性 → 准入 → Claim 审核 → Evidence Profile → 知识卡 → 发布。
  每个阶段是一次可重放的状态，自动决定写 `policy_version`，人工与自动走同一套 store。
- **主线 B 个人报告解读** —— 上传 → 抽取（停在确认前）→ 用户确认 → 逐 metric 匹配 → 按 condition 聚合成 finding。
  抽取不产生诊断，只产生候选观测与原文证据。

## 为什么这样分层

确定性内核（[[matching]]、[[card-lifecycle]]、[[evidence-strength]]、[[disposition]]、[[methodology]]、
[[publication-integrity]]、[[synthesis-eligibility]]）不依赖存储，被上下层共同引用；存储层只做事务与投影；
编排层（[[review-service]]、[[literature-ingestion]]）负责顺序；入口层（[[portal-api]]、[[health-flow-adapter]]）
只做鉴权与形状转换。依赖方向自上而下，[[review-scope]] 是对这条规则的显式例外守卫。

## 证据闸门

一条结论进入患者侧要连续通过四层：Schema 约束 → 主题闭合 → 单条 Claim → 患者文案。见 [[evidence-gate]]。
发布与能力是两个独立决定：已发布卡片默认仍是 `content_layer=context_only` / `action_status=not_available`。

## 已知缺口

`evidence_profiles.certainty` 是单值字段而非逐域 GRADE；`evals/README.md` 记录的
`genesis-evidence-evaluate` 入口在当前环境跑不通。缺口不是能力，不要当成已完成。
