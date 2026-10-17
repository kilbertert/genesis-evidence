---
title: schema
desc: 31 张表的完整一级 schema，含全部 CHECK 与部分唯一索引
hue: 40
code:
  - src/genesis_evidence/core/store/schema.py
---
# schema

建表语句本身。它是**约束的最后一层**：即使上层判断写错，schema 也会拒绝。

## raw source

schema 字符串不能 import Python 常量，因此词表的 SQL CHECK 是已声明的重复处：改动
[[methodology]]、[[card-lifecycle]]、[[consistency]] 的词表时，这里必须同提交。

## 不变量

- 表数上限 31 由 `scripts/check_schema.py` 强制。
- `knowledge_cards` 的 CHECK 要求 `published` 必须带 grade / reviewer / reviewed_at / evidence_profile_id /
  published_at / 非空正文。
- `evidence_profiles.evidence_body_complete` 被 `CHECK (= 1)` 约束：它是系统判定，不接受人工置位。
- `papers` 上三个部分唯一索引保证 DOI/PMID/PMCID 去重。
