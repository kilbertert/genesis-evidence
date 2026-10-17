---
title: publication-integrity
desc: 论文发表完整性的状态、准入，以及哪些状态禁止留下证据
hue: 190
code:
  - src/genesis_evidence/core/publication_integrity.py
---
# publication-integrity

一篇论文的发表完整性是什么、哪种状态准它进来、哪种状态禁止留下长期证据。

## raw source

这个判断曾在 `literature` 里有一个类型化枚举、一个 SQL CHECK、一个校验 `set`、一个手写读取器、
三处写入侧拒绝、三处获取侧检查，以及审核存储里七道 SQL 闸门加一个 `CASE` 分支。第六份自由副本
正是漂移的那一份：已撤稿论文的结论仍能留下来。

## 不变量

- 撤稿一经确认，既有卡片转 `stale` 并从患者接口消失——这是 [[card-lifecycle]] 的迁移，不是删除。
- `unknown` 不等于 `clear`：证据不足时不放行。
