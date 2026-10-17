---
title: persistence
desc: 事务边界、schema 与领域存储——只做持久化与投影
hue: 40
---
# persistence

所有落库的东西。这一层的职责只有两件：在一个立即写事务里完成状态迁移，以及把行投影成对外形状。
它不决定业务规则（规则在 [[kernel]]），也不编排顺序（顺序在 [[production]]）。

## 为什么单独成层

表预算是这个项目最有效的反膨胀装置：`scripts/check_schema.py` 强制表数 ≤ 31，
**新增一张表就是一次架构决定**，必须同时回答"它属于哪个簇、它的不变量为什么不能写在现有表上"。

## 已知的回边

存储层会 import `literature` 的模型与 `reports.extraction` 的报告类型，用来在写入前校验而不是先落库再校验；
`core.store → review.scope` 是因为 scope 解析是建档的唯一来源。这些回边需要解释，不是巧合——
改动它们之前先读被指向文件的 docstring。

## 边界

- 每个状态迁移在同一事务内写 `audit_events`；`actor` 由服务端决定，客户端不能提供。
- 患者侧可见性只经 [[card-lifecycle]] 的谓词生成，不手写。
