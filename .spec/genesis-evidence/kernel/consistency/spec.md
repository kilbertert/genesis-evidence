---
title: consistency
desc: 双抽取一致性裁决的词表、裁决 provenance，与 actor provenance
hue: 190
code:
  - src/genesis_evidence/core/consistency.py
---
# consistency

"两次独立抽取是否一致"的词表，以及**同一个问题在两条证据通道上的回答**：裁决来源（由决议**文本**推断）
与 actor 归属（由 `reviewer` **列**读取）。

## raw source

`'consistent' | 'needs_review'` 曾同时写在抽取报告的模型里和 `consistency_status` 列的 CHECK 里。
Python 侧收进本节点；SQL CHECK 保留字面量，因为 schema 字符串不能 import 一个常量。

actor 一侧同理：六处 `str(x or "").startswith("ai:")` 曾各自复述"谁写的"这条规则。
现在由 `is_machine_actor` 一处回答；`is_autonomous_reopenable` 是**更窄**的第二个问题——
"自主环节能否无人值守地翻掉这条决定"。

## 不变量

- 一致性裁决必须留下 provenance：哪一次抽取、谁裁的。
- 不一致不等于废弃——它把论文送进人工审核队列，而不是丢弃。
- **actor 前缀承载事实，不是命名风格。** `ai:` 是自主环节**未经全文核实**的签名；
  `source-ai:` 是 source-adjudication 分支的签名，且它的拒绝**已对照全文核实**。
  因此 `is_autonomous_reopenable("source-ai:…")` 必须为 **假**：
  一旦 `source_based` 决议落库，`_source_adjudication` 会被跳过（`review/service.py`），
  重开会把 Claim 在"没有重新核实任何东西"的那一趟里翻成 `approved`。
  机器不得静默推翻机器——这是 [[review-service]] 那条"不得放宽闸门"的镜像。
- 两条通道**允许**对同一篇论文给出不同答案（actor 通道说机器所为，文本通道说是 source-based 裁决），
  这个分歧是刻意保留的审计信息，不得合并。
- 空值 / `None` / 空白**不是**机器：未归属的行不构成"自主决定"的证据。
