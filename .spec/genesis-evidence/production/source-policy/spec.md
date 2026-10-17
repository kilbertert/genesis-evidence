---
title: source-policy
desc: 检索与下载的来源许可策略
hue: 150
code:
  - src/genesis_evidence/literature/policy.py#SourcePolicyRegistry
---
# source-policy

哪类来源可以检索、哪类可以下载，以及哪些 URL 一律拒绝。

## raw source

连接器只发元数据。CORE 在组织级许可证确认前不可下载。Sci-Hub 类 URL **直接拒绝**，不是"暂不支持"。

## 不变量

- 拒绝是抛错（`SourcePolicyError`），不是静默返回空结果——静默会让一次策略违规看起来像一次无命中。
- 许可证确认是一个显式构造参数，不由环境变量隐式打开。
