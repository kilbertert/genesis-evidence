---
title: portal-api
desc: 唯一对外证据接口——鉴权、CSP、关联 ID 与审计
hue: 290
code:
  - src/genesis_evidence/portal/api.py
---
# portal-api

本服务唯一的对外证据接口，只读。

## raw source

它**不信任**上游传来的任何判定：`abnormal_flag`、VLM 置信度、RAG 结论都只是输入数据，
不是结论。上游说"异常"，本服务仍要自己判；上游说"这是诊断"，本服务不采纳。

## 不变量

- Bearer 鉴权（RFC 6750），失败不泄漏内部细节。
- 每个响应带 UUID 关联 ID 与 CSP / 无缓存头。
- 只读：本入口不写业务状态，只写审计。
