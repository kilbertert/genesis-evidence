# #173 部署与验收记录

**日期**：2026-09-28
**主机**：36（移动云服务宿主，service-host 角色）
**范围**：genesis-evidence 商品能力退役 + health-flow 证据契约同批适配

## 部署产物（按产物身份送达，sha256 由写入门槛校验）

| 产物 | sha256 | 目标 |
| --- | --- | --- |
| `genesis_evidence-0.1.0-py3-none-any.whl` | `80988b049fadc4c6396a6212e7de1c0d8dddb8607a7ea17935e114ca6908b98f` | `/opt/genesis-evidence/artifacts/` |
| `healthflow_python-0.1.0-py3-none-any.whl` | `a780d41e067f171619eada344d95c1cc105899ff20a2e8a8d00a96cc57b7e646` | `/opt/health-flow/artifacts/` |
| `health-flow-frontend.tgz` | `428e72de820c340866d57e87c1108fb0347352ea5c0816b20447d2470b9651b0` | 解包至 `/opt/health-flow/frontend/` |

源码修订：genesis-evidence `ccbc1dd`；health-flow `5d0bec6`。

## 备份（回滚路径）

- `/var/backups/genesis-evidence/genesis-evidence.sqlite3.pre-173-20260928T140839`
- `/opt/health-flow/var/backups/healthflow.db.pre-173-20260928T140845`
- `/opt/health-flow/frontend.pre-173-20260928T141202`（上一版前端构建）

## 迁移

`health-flow-migrate-evidence`（随 wheel 分发，从项目虚拟env 调用）：

```
dry-run : reports=16 would change=10 fields_removed=99 malformed=0
run     : reports=16 changed=10 fields_removed=99 malformed=0
re-run  : reports=16 changed=0  fields_removed=0  malformed=0   ← 幂等
```

迁移后用**当前严格模型**逐条回读：**可读 13 / 不可读 3**。三条不可读的是
`schema_version="1"` 的历史 payload，**在本次改动之前就打不开**（模型只接受 `2`/`3`），
与本次变更无关；以改动前的模型复验同样失败。

## 验收

### `e2e-acceptance.sh` — 14 项全通过

| 组 | 结果 |
| --- | --- |
| A 入口可用 | portal HTTP 200；服务真实应用 |
| B 边界（负向） | 无会话访问报告/账户被拒 |
| C 内部监听不对外 | `:10005`、`:10006` 从外部不可达（负向断言） |
| D 工作台私密通道 | 无/错 bearer 被拒；正确 bearer 可用；论文 424、条件目录 12 |
| E 指标目录桥接 | 30 个 canonical metric_code |
| F 迁移数据可用 | 已有用户的报告页 188419 字节、报告元数据 status=assessed |

### `match-probe.sh` — 证据链

finding `COND_PREDIABETES` 带已发布卡 `7c574084…`（grade=low），可回溯 3 个来源
（例 DOI `10.1007/s00125-025-06560-x`）。

### 本次变更的验收契约（实测）

对 `/api/evidence/matches` 提交已确认的 `ldl_c` 异常观测：

```
recommendations        absent ok
recommendation_message absent ok
product_status         absent ok
findings 1 | condition_code COND_DYSLIPIDEMIA | evidence_items 1
card status published | sources 3
```

即：响应回到 `condition_code` + 证据文本 + 证据链接，**不含任何商品字段**。

### 患者侧页面（浏览器实测，390×844）

```
page errors           : none
evidence heading seen : true
product section count : 0
product copy present  : false
```

报告详情页正常渲染指标与风险提示，**商品区块与商品文案均不存在**。

## 残留与已知限制

1. **三条 v1 历史 payload 不可读** —— 改动前既有，未在本轮处理；放宽模型会削弱
   `extra="forbid"` 这个正好挡住本次问题的严格契约。
2. **已部署库仍保留五张商品表与数据行** —— `initialize()` 只建不删；清理是独立的一次操作，
   需单独授权与备份。新库为 26 张表，生产仍 31 张。
3. **健康数据经公开 fork 泄漏**（`all2.json`，189 KB，含报告 id 与化验原文）—— 已重写该仓
   `main` 并清除本机全部副本；GitHub 侧两个残留引用（旧 merge commit 与 `refs/pull/111/head`）
   **无法通过可用手段删除**，需 GitHub 支持介入。详见 incident 记录。
4. 明文 HTTP 与 `AUTH_COOKIE_SECURE=false` 仍然存在，直到域名与证书就位（既有跟踪项）。
