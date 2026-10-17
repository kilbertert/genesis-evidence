---
title: database
desc: SQLite 边界——立即写事务、schema 初始化与幂等迁移
hue: 40
code:
  - src/genesis_evidence/core/store/database.py#Database
---
# database

所有领域存储共享的 SQLite 边界。

## raw source

队列领取与状态迁移必须在**同一个** `BEGIN IMMEDIATE` 事务内完成。分成两个事务会让同一任务被两个 worker 领走，
而这种缺陷只在并发下出现，测试很少覆盖。

## 不变量

- `initialize()` 同时完成建表、幂等迁移与目录 upsert；它执行两次与执行一次结果相同。
- `transaction()` 是唯一的写入口；领域存储不得自行开连接控制事务。
- 数据层保留两条兼容缝（`_migrate_existing_schema` 拆分历史共享 `results` 表）——它们是已声明的迁移债，
  不是可以随时删除的旧代码。
