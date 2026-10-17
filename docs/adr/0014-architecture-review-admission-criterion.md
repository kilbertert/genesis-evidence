# 0014: 架构审查 pass 的准入判据是「重复计数必须下降」，不是删除测试

日期：2026-10-17
状态：已采纳

## 背景

`.github/workflows/architecture-review.yml` 每天跑一次，用
`improve-codebase-architecture` 这个通用 skill 找一处"加深机会"，发一条 issue。
2026-08-27 到 2026-10-15 之间它发了 19 条，其中 14 条已落地。实测这段时间：

| | |
| --- | --- |
| `src/` | 13,837 → 17,660 行（**+27%**） |
| `tests/` | 8,543 → 14,776 行（**+73%**） |
| 单次落地 diff | +173/−18 … **+862/−123** |

每条落地改动删掉 12～123 行、添加 173～862 行。19 条标题里有 17 条是同一个句子
换个名词：*"Deepen X into one `Y` module that owns …"*。

根因在判据本身。那个 skill 给的其它原则（"作用域先于扫描"、YAGNI、"接口即测试面"）
管的是**往哪里看**和**怎么表述**；真正决定"这个候选算不算一处加深"的，只有
**删除测试**——"删掉它会让复杂度集中，还是只是搬走？"。而这个问题对**任何程序里的
任何常量**都成立，所以它不构成判别器：搜索永远不会落空，只会顺着领域词表一次吐一个名词。
`card_lifecycle`、`disposition`、`evidence_strength`、`methodology`、
`publication_integrity`、`source_excerpt`、`consistency`、`synthesis_eligibility`
——然后是排队等着的另外五个名词。

可测的后果是"单一 owner"没兑现。`core/store/retirement.py`（同一个模式产出的）
开头写着"改可退役集合是这里一处编辑"，紧接着把 `SET status = 'stale'` **三处**写成
字符串字面量；而 `core/card_lifecycle.RETIRED_STATUS`——当初声明它的理由正是
"因为 `retirement.py` 和覆盖投影各自单列了它"——**没有任何模块 import**。
本该拦住它的守卫（`tests/test_card_lifecycle.py`）只扫 `Literal[...]` 片段，
它的 docstring 写着"这是卡片词表复述曾经出现过的形态"——也就是它**已经见过的**那些形态。

第二个成因是出口变成了入口：issue 的 Out of Scope 段被当成待办队列读。#304 把准入轴
交给 #305；#305 在自己的 Out of Scope 里点名 `source-ai:` 缺口并交给 #309，措辞是
*"它需要自己的 owner，本提案把它作为相邻缺口列出而不是吸收"*。pass 在写自己的下一张单。

## 决策

把这个 pass 的准入判据从"删除测试"改为**可测的成本闸**，并给"今天没有合格候选"
一个一等出口：

1. **缺陷优先。** 先找一个"代码看起来要做 A、实际做 B"的具体缺陷，并且必须给得出
   一行证据（`file:line` + 可复现的观察）。给不出这一行，就不是缺陷，是形态偏好。

2. **`proposed` 必须带实测计数**：`duplicationBefore`、`duplicationAfter`、复现两条计数的
   `countCommand`、以及 `deletedLines`。schema 层用 `.refine` 强制
   **`duplicationAfter < duplicationBefore`**——严格小于。相等意味着声明被**搬迁**而不是
   被收敛，这正是本仓库最深的失败形态（`SystemExit` 跟在人类可读消息后面、
   用模块自己的常量拼 f-string、以及在 Python 无法 import 的 SQL 里再写一遍字面量）。

3. **`skipped` 是期望结果**，并且要说明每个候选败在哪一道闸——让后来者能区分
   "安静的一天"和"坏掉的 pass"。一个说不出这句话的 pass，一定会找个名词发出来。

4. **缺陷与加深分流。** `finding` 以 `bug` 标签发布，`proposed` 以 `enhancement` 发布。
   缺陷不进 10 条积压上限（`label:"source:architecture-review" -label:bug`），
   因为上限本来是拦"没人清理的重构队列"，不该拦一个真实缺陷。

5. **发布前先打来源标签。** `#309` 是"标签已存在"的假设与 `gh` 实际行为相互作用下
   丢标签发布的，这条把事情做成显式的。

闸本身要可跑，所以新增 `.sandcastle/architecture-review/output.check.ts` 并接进
`.github/workflows/quality.yml`：一个不会失败的闸不是闸。它同时验证 schema **拒绝**
搬迁形态（相等、变高、`deletedLines: 0`、只有一份证据），也验证它**接受**一份合格提案。

## 被否掉的替代形态

**(a) 直接关掉这个 pass。** 最省事，也丢掉了它确实能做的事——它这一轮就找出了一个真实
的承重事实（`source-ai:` 前缀同时决定 actor 归属和重开闸门，见 #311/#309）。丢掉的
不是"每天一条提案"，是"一个会认真读代码的读者"。被否掉是因为它把一个**判据问题**
当成**能力问题**处理。

**(b) 只加一条"必须删多于加"的软规则，写进 prompt。** 这是最小改动，也最弱：
prompt 是建议，schema 是闸。本仓库已经吃过一次这个教训——`test_card_lifecycle.py`
里那条只扫 `Literal[...]` 的守卫，就是"我以为它会拦住"的形态。凡是能算的，就让它算。

**(c) 让 pass 自己判断"这次是否值得"。** 这正是失败的那一版。把成本估算交给
同一个只被要求"找一个候选"的角色，等于没有闸。

**(d) 把 `duplicationAfter < duplicationBefore` 定为 `<=`。** 曾认真考虑：有些真实的
加深确实会把一处声明拆成两处更清晰的声明。被否掉是因为这个 pass 已经用 14 次
落地证明了它会给"搬迁"打上"<=`"的钩；严格小于至少保证**至少一处副本真的消失**，
而放宽的收益（偶尔少一次误杀）远小于代价（回到原地）。

## 后果

**变容易的**：一条提案的质量现在可以在评审里被**算术**争论，而不是被文风争论；
"今天没有合格候选"成为一个可被接受的结论，而不是需要被解释的失败。

**变难/被限制的**：pass 的产出率会显著下降——它被设计成大多数日子报 `skipped`。
这会让"这个 pass 还活着吗"变得需要看 run 摘要才能回答，这是刻意接受的代价：
一条假提案的成本（评审一个下午 + 一个几百行的 PR + 一条永久守卫 + 一条会生出下一条的积压）
高于一次空跑。

**没有被这个决策解决的**：已经落地的 14 个 `core.*` owner 模块里，
"声明被搬迁而非收敛"的残留**仍在**——例如 `retirement.py` 的三处 `'stale'` 字面量、
以及 `core/store/review.py` 里仍在写的若干卡片状态字面量（`retirement.py` 自己那套
`SUPERSEDABLE_STATUSES`/`PUBLISHED_STATUSES` 是第二份本地词表）。
本 ADR 只保证**不再新增**这类残留，不假装已经清理完毕。
清理它们的正确时机是"有人因为具体理由要改其中一个词表"，而不是再开一条同类提案。
