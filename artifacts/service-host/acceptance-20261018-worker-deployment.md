# 论文抽取 worker 上线记录与一次生产误操作（2026-10-18）

**主机**：36（移动云服务宿主）
**范围**：ADR 0015 的修复在生产上生效；抽取 worker 首次以 systemd unit 部署
**含**：一次我造成的生产写入事故、它的恢复、以及恢复路径的缺陷

## 一、部署产物

| 产物 | 目标 | 说明 |
| --- | --- | --- |
| `genesis-evidence-worker.service` | `/etc/systemd/system/` | `enabled` + `started`；sha256 `2b6230a0…` |
| `var/review.env`（追加 5 项） | `/opt/genesis-evidence/var/` | 见下 |
| `var/paper-ai.key` | `/opt/genesis-evidence/var/` | mode 600，属 `genesis-evidence` |

`review.env` 新增：`PAPER_AI_BASE_URL`、`PAPER_AI_MODEL=deepseek/deepseek-v4.1-flash`、
`PAPER_AI_TIMEOUT_SECONDS=600`、`PAPER_AI_MAX_TOKENS=32768`、`PAPER_AI_API_KEY_FILE`。

**模型变更，需记录**：生产既有 182 篇抽取由 `deepseek-v4-pro-0813` 产生，本次改用
`deepseek/deepseek-v4.1-flash`。**新旧数据不同池**，比较时必须按模型分开。

## 二、凭据链路的三次验证

配置能加载不等于能用。实际经历：

1. **文件格式错**：写成 `api_key\n<value>`（单列）。`_api_key_from_file` 用
   `csv.reader`，要求**第 0 列是字段名、第 1 列是值**，单列文件被读成一行一列，
   判为"no apiKey field"。修正为 `api_key,<value>`。
2. **配置加载**：`ArkPaperAnalyzer.from_env()` 在宿主上返回
   `api_key_configured: True`、`model: deepseek/deepseek-v4.1-flash`。
3. **真实调用**：从宿主直接 POST provider，**HTTP 200**，返回该模型。

## 三、一次生产写入事故（我的操作失误）

**做了什么**：为了在装 unit 前"验收"，我**直接在生产库上**手工运行了一次 worker
（`GENESIS_EVIDENCE_ACTIVE_TOPIC_ID=nutrition-electrolytes`，`timeout 240`）。

**为什么是错的**：迁移契约要求「前一个部署继续运行，直到新部署通过验收」——
试跑应当发生在副本上，而不是生产。

**后果**（当时观测到）：

| 现象 | 原因 |
| --- | --- |
| `06d80e1a` 被抽取并 `ft=excluded` | 手工运行实际处理了它 |
| `8392f30c` → `WorkerInterrupted` | `timeout` 掐断在 `extraction_a` 中途 |
| `95148543` 停在 `running` | 进程被杀，状态未回收 |
| 其余 `ta` 仍为 NULL | 只跑到前几篇 |

**备份为什么没救回来**：我在 worker 启动后**约 16 秒**才做备份
（`05:28:18 +0800` = `21:28:18 UTC`，而 job 的 `started_at` 是 `21:28:02 UTC`），
所以那份"恢复前备份"**已经包含**事故产生的行。拿它恢复，等于把假 `running`
恢复进生产。**备份必须在服务停止后做**——这条是既有记忆
（`mem-20260930-ranlei-010`）里写着的，我这次没有照做。

**恢复**：用事故前的 `VACUUM INTO` 快照恢复 → 校验 `extractions 183 / claims 2361 /
cards 52 / topic ta 0` 全部等于基线 → 单独回收残留的 `running` job（它是
`started_at` 被改过、但 `model`/`run_id`/输出全空的空壳，改回 `queued`）。

**恢复路径的缺陷**：`recover_running_extraction_jobs` 只在进程**正常启动**时运行；
被 `SIGKILL` 的进程不会触发它。`systemd` 的 `Restart=on-failure` 能救这种情况，
但手工 `timeout` 杀掉的不在 systemd 视野里。这是一条应该在运维脚本里显式处理的路径。

## 四、正式部署（按契约重做）

1. **停服务**（`genesis-evidence-review`、`genesis-evidence-portal`）
2. **在停止状态下取一致快照**：`/tmp/ge-prod-preworker-20261017T215340Z.sqlite3`，
   sha256 `78ca4bea…`，校验 `extractions=183 / running=0 / queued=8`
3. **装 unit 并启动**，单主题限定（`GENESIS_EVIDENCE_ACTIVE_TOPIC_ID`）
4. 首次启动失败：`PermissionError` 于 `var/genesis-evidence.sqlite3.extraction-worker.lock`
   —— 该锁文件被我先前的手工运行创建为 **root 所有**。改为服务身份后重启成功。

**§3 之前先在宿主机副本上验过**：用 `VACUUM INTO` 取一致副本，在副本上跑 worker，
观测到 `06d80e1a / 8392f30c / 95148543` 完成抽取+审核并给出具名排除
（`wrong_outcome` / `wrong_population` / `wrong_comparator`），与开发机副本上的结论一致。

## 五、验收判据

worker 处于 `active`、`NRestarts` 稳定、journal 无新错误；生产库开始出现
`title_abstract_decision` 与 `full_text_decision`。**本记录不代表主题会产出卡片**——
`nutrition-electrolytes` 的 7 篇有全文论文在开发机副本上全部因研究设计/人群/结局
不符被排除，3 篇版权受限。worker 上线证明的是**流程能跑完**，不是这个主题有证据。

## 六、未解决

- 生产上 `var/review.env` 的 `PAPER_AI_MODEL` 由 `deepseek-v4-pro-0813` 改为
  `deepseek-v4.1-flash` 是**模型变更**，需要一次独立的验收才能说新模型的抽取质量
  与旧模型可比。本次只证明它可用。
- `nutrition-ckd-risk` 的 1 篇待取全文不在本次单主题限定内。
- ADR 0012 决策 6（身份拆分）仍未执行。
