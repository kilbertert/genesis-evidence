---
title: evaluation
desc: 抽取评估指标与黄金集比较
hue: 290
related:
  - evals/README.md
  - src/genesis_evidence/reports/evaluation.py
---
# evaluation

报告抽取与论文抽取的质量度量：拿预测结果与黄金集逐项比较。

## 已知缺口

`evals/README.md` 里写的 `genesis-evidence-evaluate` 命令在当前环境**跑不通**：
`pyproject.toml` 的 `[project.scripts]` 里没有它，但残留的 editable 安装 dist-info 仍声明这个入口点，
而 `.pth` 指向已删除的工作树。所以 shell 能找到命令，执行后报 `ModuleNotFoundError`。
根治办法是把入口点注册修对，而不是继续用 `python -m` 绕过。

## 可用形式

```bash
uv run python -m genesis_evidence.reports.evaluation <gold.jsonl> <predictions.jsonl>
```

本节点不 `code:` 拥有实现文件，因为它衡量的是别处产出的结果，不是流程的一环。
