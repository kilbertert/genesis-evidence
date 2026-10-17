---
title: components
desc: 成分目录与成分形式的规范标识
hue: 190
code:
  - src/genesis_evidence/core/components.py
---
# components

证据分组轴的对象定义（ADR 0007）：成分与成分形式的目录。

## raw source

分组轴是成分，不是疾病，也不是论文。这条选择决定了"同一成分的多篇论文可以合成"，也决定了一条自由文本
不能靠子串猜测变成成分身份。

## 不变量

- `component_key` 来自本节点的目录表，是唯一合法写法。
- 不得在别处另造标识，也不得按自由文本子串推断成分。
