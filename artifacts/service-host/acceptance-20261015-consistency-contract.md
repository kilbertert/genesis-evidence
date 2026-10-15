# #291/#298 部署与一致性契约首次试跑记录

**日期**：2026-10-15
**主机**：36（移动云服务宿主）
**范围**：论文抽取的 8192 预算修复（#291）、一致性阶段逐字引文契约（#298）

## 部署产物

| 产物 | sha256 | 目标 |
| --- | --- | --- |
| `genesis_evidence-0.1.0-py3-none-any.whl` | `743ba3419991b97dbe688daa5c186cf261f7666dda566bc08bc4225b123c98a1` | `/opt/genesis-evidence/artifacts/` |

源码修订：`57cbf81`（`main`）。装后回读 `direct_url.json`，其哈希等于交付标识。

**回滚点**：上一个产物 sha256 `b5bf52202373cd0ab2fbf8828a4e4dbaae9edd1265249d07ecf318dbded2b5bb`
（源码修订 `a5ceb1f`，PR #271，部署于 2026-10-09）。回滚 = 用该 sha 重新安装 + 重启两个 unit。
旧文件仍以 `.pre-271-20261009T1932Z` 命名的副本保存在 `artifacts/`。

自 #271 以来，**运行时代码只有两处变更**：`literature/ai_extraction.py`
（#291 去掉 `min(self._max_tokens, 8192)`；#298 增加 `_require_verbatim_evidence` 与
两个 prompt 的逐字要求），以及 `core/source_excerpt.py` 接收从 `review/service.py`
移来的 `_source_evidence_fragments`。其余合并（#292/#299/#301/#303 模板升级、
#294/#295/#300/#306 文档）不改运行时代码。

## 验收

| 断言 | 结果 |
| --- | --- |
| `genesis-evidence-portal` / `genesis-evidence-review` 重启后 `is-active` | active |
| `/health` @ 10005 / 10006 | 200 / 200 |
| 重启后 `journalctl -p err` | 无条目 |
| 移位函数在宿主构建里可导入（`source_excerpt._source_evidence_fragments`、`review.service` 转出、`classify_issue_priorities`、`_require_verbatim_evidence`） | 全部可调用 |
| **`review_guidance` 在真实生产库上端到端** | 3 篇各返回 7 / 3 / 15 条 issue（其中 1 篇 2 条 material） |

最后一项是本次回归风险所在：`_source_evidence_fragments` **跨模块移动**了，而
`/health` 不覆盖该导入路径。用生产库直接驱动 `ReviewStore.get_review_item` 证明
移位后整条 guidance 路径（含新提取的 `classify_issue_priorities`）在宿主构建上可用。

## 一致性契约首次在生产数据上执行

**7 篇，全部 `nutrition-electrolytes` 的 `queued` 作业。** 因为宿主上的两个服务都不
调用抽取路径（`review/api.py` 只有 `retry` 的入队接口，没有 worker；宿主 `var/review.env`
也不含 `PAPER_AI_*`），这次试跑在**开发机**上、对**生产语料副本**运行
（`scripts/local_extraction_batch.py --apply --queued-jobs --topic nutrition-electrolytes
--keep`），未写生产库。

| 断言 | 结果 |
| --- | --- |
| 抽取完成 | **7/7，零失败** |
| 墙钟 | 1433s（205s/篇） |
| 写出的 claims | 84 |
| 一致性阶段被契约拒绝而整篇失败 | **0**（与分布实测的 1.4% 相符，7 篇样本内为 0） |

**7/7 都返回 `needs_review`**——与既有认知一致（两个抽取通道必然有差异）。

准入结果：**1 篇 `internally_admitted`（8a3be303），6 篇 `excluded`**。拒绝**不是契约
误伤**，而是审核给出的具名科学排除：`wrong_outcome` ×3、`wrong_population` ×1、
`wrong_timing_or_setting` ×1、`wrong_intervention_or_exposure` ×1。这 6 篇在 10-09 入队
时是 eligible 的，是**跑完之后**被判定不该收——队列里没有垃圾，但也没有人提前判过它们。

## 这次试跑真正暴露的东西：卡片一张没出

7 篇跑完，**`knowledge_cards` 新增 0**。连唯一准入的那篇也停在：

```
status: waiting_for_complete_evidence_body
reason: topic screening ledger is incomplete or has an invalid exclusion reason
```

根因是 `nutrition-electrolytes` 这个主题的**筛选台账不完整**：

| 状态 | 篇数 |
| --- | ---: |
| 标题摘要已判、全文已取、`ft=excluded`（有原因） | 6 |
| 标题摘要已判、全文已取、`ft=included` | 1 |
| **标题摘要从未判过（`ta=NULL`，`full_text_retrieval_status=pending`）** | **3** |

那 3 篇是 `c868a8ff`、`7d211d15`、`cf581a18`：**既没有全文，也没有抽取作业**——
它们从未进入抽取队列，所以「跑完那 7 篇」不可能让台账闭合。

对基线副本的对比证实因果：**试跑前该主题 10 篇全部未判**（`ta=NULL`），试跑后剩下 3 篇。
也就是说，抽取流程顺带把它经手的 7 篇筛完了，而 3 篇没人碰过的仍是空白。

**结论：`nutrition-electrolytes` 要出卡片，缺的不是抽取，是那 3 篇的标题摘要裁决。**
这是一个「队列不完整」的问题，不是「模型判不了」的问题——与 ADR 0012 决策 5 记录的那类
缺口同源：先看上游有没有把该做的做完，再谈下游能力。

## 未做与已知边界

- **未写生产库。** 本次是副本运行；生产库仍是 183 篇抽取、7 篇 `queued`。
- **未部署抽取 worker。** 恢复抽取仍按既定决策待单独决定；本次只证明它在当前构建上
  单主题跑得通。
- **生产 `var/review.env` 不含模型凭据**，因此宿主上目前**跑不了**抽取；这是它当初未部署的
  第二个原因，已确认。
- 6 篇的 `ft=excluded` 行在本次前后都显示 `primary_exclusion_reason` 为空，
  但同一批的 `autonomous_screening_completed` 记录了具名 reason——两者关系未核，
  记为待查项而非缺陷。
