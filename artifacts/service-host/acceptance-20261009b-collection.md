# #269/#270/#271 部署与验收记录

**日期**：2026-10-09（第二轮）
**主机**：36（移动云服务宿主）
**范围**：主题计划 + 检索入口（#269）、单篇完整性核查容错（#271）

## 部署产物

| 产物 | sha256 | 目标 |
| --- | --- | --- |
| `genesis_evidence-0.1.0-py3-none-any.whl` | `b5bf52202373cd0ab2fbf8828a4e4dbaae9edd1265249d07ecf318dbded2b5bb` | `/opt/genesis-evidence/artifacts/` |
| `collect_topics.py` | `9fcb30c8a127f9adf1b3d09e98d1c9d427903b8b75832355c2a80117e9bdf40d` | `/opt/genesis-evidence/ops/` |

源码修订：`a5ceb1f`。装后回读 `direct_url.json`，其哈希等于交付标识。

**检索脚本为何放在 `ops/` 而不是进 wheel**：它的工作是「拿库、发请求、写库」，
不是提供 import；unit 的 `ExecStart` 也证明运行形态是命令行而不是入口点。放进
wheel 会在每次发布时、用 `pip install --force-reinstall --no-deps` 去覆盖一个
正被运维编辑的文件——那是把部署通道和运维通道混在一起。按产物身份单独送达、
落在 `ops/`（与既有的 `rebuild_evidence_bodies.py`、`match-probe.sh` 同处）。

**一个安装缺陷**：脚本以 root 送达后是 `-rw-r----- root`，服务身份读不到它，
`su … genesis-evidence` 报 `Permission denied`。改为 `chown genesis-evidence:`
+ `chmod 750`。凡是以服务身份执行的 `ops/` 脚本都要这一步，**新脚本的送达清单里
应默认包含它**。

## 备份（回滚路径）

| 对象 | 路径 |
| --- | --- |
| 生产库 | `/var/backups/genesis-evidence/genesis-evidence.sqlite3.pre-271-20261009T1932Z`（sha256 `e41ed72b85f811a336d5b385d796f86bf360fb71c6c50aab77fec35605960df1`） |
| 上一版 wheel | `/opt/genesis-evidence/artifacts/genesis_evidence-0.1.0-py3-none-any.whl.pre-271-20261009T1932Z` |

备份在服务停止之后取。

## 验收

| 项 | 结果 |
| --- | --- |
| `/health` | 200 |
| 鉴权边界（无 key / 对 key） | 401 / 200 |
| 指标目录 | 66 |
| 回归：`ldl_c` 异常 | finding `COND_DYSLIPIDEMIA`，已发布卡正常送达 |
| 新能力在位 | `topic_plans` 10 条；`IngestionSummary.unchecked_integrity` 存在 |
| 脚本 dry-run（宿主上） | 正常出计划与查询 |

## 第一次生产检索

```
nutrition-electrolytes: created and locked topic bb47ef78-d0d1-4a37-b9a1-d8828581ee54
nutrition-electrolytes: run 29ba703c — discovered 10, full texts 7, queued extractions 7,
                                     skipped 1, failed 2
```

- `collection_runs.status = 'completed'`；主题落库并锁定；
- **10 篇里 8 篇完整性状态为 `clear`**，2 篇未取到全文（`failed_full_texts`）；
- 论文总数 424 → **432**；
- 进抽取队列的作业从 1 增到 **8**。

这是主线 A 的「抓论文」第一次在生产上**跑完一轮**。此前同一入口三次都以
`status='failed'` 收场（#270）。

## 未闭合

1. **抽取 worker 仍未部署**，8 篇的抽取作业**排在队列里不会动**。这是既定决策
   （单主题低速验收），本次未改——恢复抽取需要单独的一次低速验收。
2. **另 9 个主题未开检索**。本次只跑了电解质一条：新入口第一次上生产，先做一个
   真实验证再铺开。
3. **#270 的预算取舍仍开放**（超时/重试与那条上游的尾部延迟），见该 issue 的评论。
