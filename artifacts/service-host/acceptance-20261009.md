# #239/#251–#263 部署与验收记录

**日期**：2026-10-09
**主机**：36（移动云服务宿主，service-host 角色；`36.156.159.175`）
**范围**：健康问题目录扩表（12 → 30）与六个域的报告触达（PRD #239，T1–T9）+ 成分分轴（ADR 0007）此前已在使用但**未计入部署记录**的部分

## 部署产物（按产物身份送达，写入门槛校验 sha256）

| 产物 | sha256 | 目标 |
| --- | --- | --- |
| `genesis_evidence-0.1.0-py3-none-any.whl` | `87ae463f890a83935b70f3dbbcde95026ebcb012e46476a05fbc7e9dff702da5` | `/opt/genesis-evidence/artifacts/` |

源码修订：genesis-evidence `ec9e451e1cbbf097be762ace1fb18b000e407e31`。

**上一次记录（2026-09-28）记的是 `ccbc1dd`**。自那以后到达生产的是 DR 0007 的成分分轴（#224 起）
与本次 PRD #239 全部九个切片。本次部署把它们一并送达，因此本记录同时覆盖两者。

安装方式：`.venv/bin/python -m pip install --force-reinstall --no-deps <wheel>`，装前先校验落盘
产物的 sha256 与上表一致，不一致则拒绝（退出码 77）。装后回读 dist-info 的 `direct_url.json`，
其记录的哈希等于交付标识——**这证明"装上的就是送来的那个"**，而不是看文件名相同就假定相同。

## 备份（回滚路径）

| 对象 | 路径 |
| --- | --- |
| 生产库 | `/var/backups/genesis-evidence/genesis-evidence.sqlite3.pre-263-20261009T063825Z`（sha256 `93c406f83dda3aa756d54ba6f3eb62dfb161a18127d9828b9890f6734a645a56`） |
| 上一版 wheel | `/opt/genesis-evidence/artifacts/genesis_evidence-0.1.0-py3-none-any.whl.pre-263-20261009T063825Z` |

**备份在服务停止之后取**：SQLite 在 WAL 模式下运行中复制可能拿到只存在于 `-wal` 里的事务。

## 停机窗口内的实测（不是推理）

新代码的 `Database.initialize()` 会改写生产库，所以在动真库之前先在**副本**上跑了一遍：

```
BEFORE  cards: {'published': 52, 'approved': 19, 'stale': 155}  conditions: 12
AFTER   cards: {'published': 52, 'approved': 19, 'stale': 155}  conditions: 30
DELTA   published: 0
```

即：**迁移只新增 18 个 condition，不退役任何已发布卡**。另外单独核对了新退役规则
（`retire_ungoverned_profile_cards`）的命中集：生产库中**0 张**已发布卡的 profile 无治理主题，
所以那条一次性迁移对本次是空操作。

## 验收

### 服务端（`dev-host exec 36`，隔离于公网入口）

| 项 | 结果 |
| --- | --- |
| `/health` | 200 |
| 鉴权边界（无 key / 错 key / 对 key） | `401 / 401 / 200` |
| 指标目录 | **66**（部署前 30） |
| 回归：`ldl_c` 异常 | finding `COND_DYSLIPIDEMIA`，卡片 `published`/`low` 正常送达 |
| 新增可达性：`sodium` 异常 | 200，finding 0（尚无卡），claimant `COND_ELECTROLYTE_DISTURBANCE`——**认领到了，不是 400** |
| 覆盖矩阵 | 86 行；`planned 53 / published_context 28 / published 4 / blocked_very_low 1`；新 condition 均在列且为 `planned` |
| 监听 | `127.0.0.1:10005`、`127.0.0.1:10006` 均只绑环回 |

「sodium 认领到 condition 却没有卡」正是 PRD 说的**放开的是覆盖范围、不是确定性等级**：
目录认得这个名字了，要不要说话由证据闸门决定。

### `e2e-acceptance.sh`（公网入口，12/13）

12 项通过，1 项**因工具限制未通过**：

- 通过：公网 portal 200 且是真实应用；无会话访问被拒（负向）；`:10005`/`:10006`
  从外部不可达（负向）；工作台私密通道下无/错 bearer 被拒、正确 bearer 可用；
  迁移论文 424 篇可读；条件目录 30；指标目录经 portal 桥接为 66。
- 未通过：`report page / metadata` 一项。原因是 `e2e-acceptance.sh` 在**本地**查找
  `/tmp/e2e-state.json`，而该状态文件由 `mint-probe-session.py` 写在**服务宿主上**——
  两者不是同一个 `/tmp`。这是脚本本轮之前就有的局限，不是本次部署的回归。
- 该项的内容改在服务宿主上直接执行（等价断言，实测）：

  ```
  report=29 file=1
  page http=200 bytes=188419
  metadata http=200  status=assessed  id matches=True
  ```

  迁移数据可用，结论与公网那一段一致。

**探针会话已清理**（`removed probe session(s): 1`），转移的两支脚本已从宿主 `/tmp` 删除。

## 残留与已知限制

1. `e2e-acceptance.sh` 的 state-file 路径在本地与宿主之间不一致（见上）。修法是让它经
   `dev-host` 读取，属脚本改动，未在本轮做。
2. 生产库仍有 **31 张表**，代码建 **26 张**：多出的 5 张是 ADR 0006 退役的商品表，
   `initialize()` 只建不删。清理需单独授权与备份。
3. 明文 HTTP 与 `AUTH_COOKIE_SECURE=false` 仍在，直到域名与证书就位（既有跟踪项）。
4. 生产库的已发布卡分级分布未变（`low 47 / moderate 5`），患者侧能力层级仍为
   `context_only` + `not_available`——本次放开的是**覆盖**，不是行动内容。
5. 论文抽取 worker 仍停用（既定决策），本次未改。
