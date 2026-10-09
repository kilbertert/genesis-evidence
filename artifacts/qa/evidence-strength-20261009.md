# 证据强度词表收敛为单一 owner（#238）：运行服务验收（2026-10-09）

**提交**：`refactor/evidence-strength-vocabulary`（基于 `ec9e451`）
**环境**：开发宿主，临时 SQLite + 临时对象存储，真实 `genesis-evidence-review`
进程（`127.0.0.1:8199`），真实浏览器（Playwright/chromium，1440×900）
**驱动脚本**：`var/verify-238.py`（gitignored，属脚手架；取证产物见下）

## 结论

**通过。** 工作台的证据确定性选择器与可见性说明句现在都由
`core.evidence_strength` 提供：选择器从 `GET /api/review/strength-vocabulary` 取词表，
说明句从同一载荷取文案。**曾经说错的那句话已消失**——页面不再声称 `low` 不发布到患者端，
而 `low` 恰恰**会**作为可见背景卡发布（`PATIENT_VISIBLE_STRENGTHS` 含 `low`）。

## 观测到的证据

| # | 驱动作 | 观测结果 |
| --- | --- | --- |
| 1 | `GET /api/review/me` | `200`（密钥与实例匹配，排除"应用坏了"的假象） |
| 2 | `GET /api/review/strength-vocabulary` | `200`，载荷 `values/labels/patient_visible/action_threshold/visibility_guidance` |
| 3 | 浏览器：`sessionStorage` 注入密钥 → 点「连接」 | 认证审核人显示为 `ranlei`，论文队列渲染 |
| 4 | 点队列中的论文 → 展开步骤 5 与「人工接管知识卡草稿」 | `<select id="profileCertainty">` 选项 = `high/moderate/low/very_low`，与端点返回值逐一相同 |
| 5 | 读步骤 5 那段 `p.muted` | 说明句 = `visibility_guidance` 原文（见下） |
| 6 | 全页 `content()` 中查 `blocked_low_certainty` | **不存在**（无产者状态标签已移除） |

端点返回的句与页面渲染的句**字节相同**：

```
极低确定性内容不会发布到患者端；低确定性内容仅作为证据背景卡发布，行动建议需达到中等或高确定性。
```

旧句是「低或极低确定性内容不会发布到患者端」——它对 `low` 说谎。新句由两个阈值集合
派生，因此"说明句与闸门不一致"这一形状无法再被写出来。

## 与单元测试的分工

单元测试钉住的是纯函数与源码扫描（词表、排名、阈值、标签、SQL `CHECK` 提取比对、
禁止重新声明词表/排名/阈值、工作台取词表而非抄词表）。本记录钉住的是**它们合起来在
运行服务上是否真的成立**：词表端点确实被页面消费，说明句确实是服务端下发的那一句。

## 确定性检查

`ruff check .`、`uv run pytest`、`scripts/check_scope.py`、`scripts/check_schema.py`
（26 表）、`scripts/check_patient_copy.py`、`scripts/check_review_workbench_layout.py`
全部通过；`core.evidence_strength` 的 `demo()` 自检通过。

## 取证产物

- `var/verify-evidence/results.json` — 断言结果
- `var/verify-evidence/strength-vocabulary.json` — 端点原始载荷
- `var/verify-evidence/workbench-step5.png` — 打开步骤 5 的页面（选择器与说明句同框）

产物落在 gitignored 的 `var/` 下；本文件是进入版本控制的那一份结论。
