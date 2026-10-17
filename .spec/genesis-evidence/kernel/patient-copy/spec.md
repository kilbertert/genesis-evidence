---
title: patient-copy
desc: 患者可见文案的禁用词闸门
hue: 190
code:
  - src/genesis_evidence/core/patient_copy.py
---
# patient-copy

患者可见正文的禁用词表与校验函数。它是"不得成为诊断、商品推荐、剂量或治疗指令"这句话的可执行形式。

## raw source

词表是唯一来源，但**调用点覆盖范围不同**：运行时校验所有写入路径，CI guard 只扫描固定的文件路径集合。
这处不对称是已知缺口——新增写正文的路径时，CI 扫不到它，只有运行时在挡。

## 不变量

- 已发布正文使用研究语言（"研究提示"），不是医嘱语言。
- 新增任何写 `patient_visible_body` / `action_message` 的路径，必须显式经过本节点校验。
