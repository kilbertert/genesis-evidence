# 服务宿主验收：#331 T3 canary + #332 T4 证书到期闸门

**执行时刻**：2026-10-20T05:54Z（CST 13:54）起，约 13:40–14:00 CST。
**构建来源**：`33d7745`（PR #336 合并后的 `main`），落盘资产 sha256 逐项记录在下方。
**环境**：服务宿主 `36` / `yidong-36`（移动云，`36.156.159.175`，机器自述 `ecs-c675-0002`）。
**范围**：只新增本项目的文件。未改任何公司站点的 vhost、证书或配置。

---

## #331 T3：canary

### 1. DNS

| 项 | 值 |
| --- | --- |
| 记录 id | **`8869a9e6770cc8fc3974244e0d946198`** |
| 名称 / 类型 / 值 | `health.ranlei.work` A `36.156.159.175` |
| 代理 / TTL | `proxied=false` / `60` |
| 公开解析实测 | `1.1.1.1 → 36.156.159.175`，`8.8.8.8 → 36.156.159.175` |

**回滚按 id 删除，不按名字删。**

### 2. 三次安装（顺序是被迫的）

`nginx` 不接受只装一半的完整文件（443 块会因证书不存在让 `-t` 失败），而 HTTP-01 又要求
80 上先能答。所以：

| 步骤 | 文件 | sha256 |
| --- | --- | --- |
| ① 只含 80 块（临时） | `/www/server/panel/vhost/nginx/health.ranlei.work.conf` | `b0e11eb4…d0e2` |
| ② 签发证书（下方） | — | — |
| ③ 完整入口（80+443） | 同上，覆写 | `1141bc97…b69b6` |

`dev-host write` 在 ③ 用了 `--if-sha256` 乐观锁（旧值 = ① 的 sha），确保覆写的是自己刚装的那份。

**`nginx -t` 判据：无新增警告。** 基线本来就不干净：

```
nginx: [warn] duplicate MIME type "text/html" in /www/server/panel/vhost/nginx/dify-console-10008.conf:163
nginx: configuration file /www/server/nginx/conf/nginx.conf test is successful
```

三次 `-t` 前后都只有这**一条**既有警告，没有新增。reload 用 `nginx -s reload`（pid 文件），
未用 `systemctl reload nginx`（该 LSB unit 自 2026-09-11 起 failed 而 nginx 一直正常）。

### 3. 证书

| 项 | 值 |
| --- | --- |
| 来源 | Let's Encrypt（正式；staging 先跑通过） |
| key type | ECDSA（`id-ecPublicKey`，`ecdsa-with-SHA384`） |
| subject / SAN | `CN=health.ranlei.work` / `DNS:health.ranlei.work` |
| **notAfter** | **`Jan 18 04:43:21 2027 GMT`** |
| 信任链 | `openssl s_client … Verify return code: 0 (ok)`；`curl` 不带 `-k` 亦 200 |
| 续期钩子 | `renew_hook = /opt/genesis-evidence/ops/cert-deploy-hook.sh`（**按 lineage**，不在全局目录） |

全局 `/etc/letsencrypt/renewal-hooks/deploy/` 仍只有公司原有的 `10-reload-nginx.sh`，未加入任何东西。

staging 先跑通（issuer `(STAGING) Artificial Amaranth YE1`），证明 webroot、80 块、路径对得上，
再正式签（issuer `YE1`）。webroot `/www/wwwroot/health.ranlei.work` 为 `755 root:root`。

### 4. 验收判据

| 判据 | 结果 |
| --- | --- |
| 公网返回**真实应用内容**（判内容不判状态码） | `200`，标题 `健康流 HealthFlow`，含 `/assets/index-WUsQcwGd.js` |
| 无关 Host 打同一入口，内容**不是**本应用 | 443：`curl` exit 52（TLS 握手被拒，catch-all 的 `444` 语义）；80：`0.default.conf` 返回 `200` + 一个写着 `404 Not Found` 的静态页，**不含**本应用内容 |
| 公司站点按各自 Host 改动前后均正常 | `dify.ranlei.work` 307、`xyadmin` 200、`xyapi` 200、`xyh5` 200、`xyiot` 200（443 by SNI；改动前后一致） |
| `certbot renew --dry-run` 通过 | `Congratulations, all simulated renewals succeeded`（`--cert-name health.ranlei.work`） |
| **手工执行一次 deploy hook** | `RENEWED_LINEAGE=/etc/letsencrypt/live/health.ranlei.work` → `rc=0`，`nginx -t` 通过后 reload；证书已拷到 `/www/server/panel/vhost/cert/health.ranlei.work/`（签发时钩子即已执行过一次） |
| 钩子负控 | 外来 lineage（`dify.ranlei.work`）→ `rc=1`；未设 `RENEWED_LINEAGE` → `rc=1` |
| 访问日志存在、可读、**不含 `ticket=`** | `/www/wwwlogs/health.ranlei.work.log`，211 行，`grep -c 'ticket='` = **0** |
| logrotate | 装到 `/etc/logrotate.d/health-ranlei-work`；`logrotate -d` 通过，只覆盖我们那两个文件 |
| **旧入口仍可用（回退路径未破坏）** | `http://36.156.159.175:10007/` → `200`，含 `健康流` |

### 5. 编队外可达性（两层分开记录，不互相冒充）

**编队内跨主机**：本机（dev host，编队内）直接请求 → 上表内容断言。

**第三方多地探测**（`check-host.net`，6 节点，request id `5229dd58k3cd`）：

| 节点 | 结果 |
| --- | --- |
| ch1 Zürich | OK 200，解析到 `36.156.159.175` |
| es1 Spain | OK 200 |
| il2 Israel | OK 200 |
| se2 Sweden | OK 200 |
| vn1 Vietnam | OK 200 |
| au1 Australia | **Connection timed out** |

5/6 节点从编队外拿到 200。**au1 超时如实记录，不掩盖**：单节点连接超时在跨境链路上是常见现象，
但它确实是一个未能从该地点证明可达的记录。另用 `r.jina.ai`（编队外抓取器）取回页面正文，
返回的是患者门户真实的「请从商城入口进入」文案，与本机所见一致。

---

## #332 T4：证书到期闸门

### 落盘产物（写前 / 写后 sha256 一致）

| 用途 | 路径 | sha256 |
| --- | --- | --- |
| 脚本 | `/opt/genesis-evidence/ops/cert-expiry-check.sh` | `70ca4b5b…36ae2` |
| service | `/etc/systemd/system/cert-expiry-check.service` | `ec8bfab6…e75bb5` |
| timer | `/etc/systemd/system/cert-expiry-check.timer` | `b7d718c8…b98e` |

三个都以 `dev-host cp --artifact-sha256` 落盘，写前写后 sha256 相同。脚本 `chmod 755`。

### 启用状态

```
enabled / active
NEXT: Wed 2026-10-21 00:21:21 CST  (daily, Persistent=true, RandomizedDelaySec=1h)
```

### 它会响（三重证明）

1. **自检 7/7**（在宿主上跑的是落盘的那份，不是仓库里的）：
   ```
   ok  expiring sample is rejected (rc=1)
   ok  fresh sample passes
   ok  unreadable path fails closed (rc=1)
   ok  an observed lapse does not judge the run (rc=0)
   ok  the observed lapse is still reported
   ok  an owned lapse judges the run (rc=1)
   ok  an absent own certificate fails the sweep (rc=1), present one passes
   ```
2. **真实台账**（`systemctl start cert-expiry-check.service`，`Result=success`，不在 `--failed` 里）：
   ```
   EXPIRING*  api.qumall.qushiyun.com   Oct 30 … 10 day(s) left [observed]
   ok         dify.ranlei.work          Jan 13 … 85 day(s) left [observed]
   ok         *.ranlei.work             Aug  6 2041 … 5404 day(s) [observed]
   ok         health.ranlei.work        Jan 18 2027 … 89 day(s) [owned]
   ok         testrefator1/2, xyadmin, xyapi, xydiy, xyh5, xyiot … [observed]
   ```
   `health.ranlei.work` 被正确识别为 `[owned]` 且判为 ok。`api.qumall.qushiyun.com` 剩 10 天，
   按两类化只记录不判负（即 #335 单独处理）。
3. **unit 真的会进 failed**：临时建了一个只含该判定的探针 unit，喂一张 10 天到期的自签证书
   并声明为 owned，`systemctl start` → `rc=1`、`is-failed` = `failed`、出现在
   `systemctl --failed` 里。探针随后删除并 `reset-failed`，`daemon-reload` 后确认已不存在。

### 未做的事（刻意的）

- 没有为了让闸门变绿去改 `OWNED_DIRS` 或 `OWNED_DIRS_OVERRIDE`。本次 T3 的证书已就位，
  所以裸运行本就是绿的；这也顺带证明了 `health.ranlei.work` 在 `OWNED_DIRS` 里确实被覆盖。
- 没有碰 `api.qumall.qushiyun.com`。

---

## 回滚路径

| 项 | 命令 |
| --- | --- |
| 入口配置 | 删除 `/www/server/panel/vhost/nginx/health.ranlei.work.conf`，`nginx -t && nginx -s reload` |
| DNS | 按 **record id `8869a9e6770cc8fc3974244e0d946198`** 删除该 `A` 记录 |
| 证书 | 不影响旧入口；`/etc/letsencrypt/live/health.ranlei.work/` 可保留或 `certbot delete --cert-name health.ranlei.work` |
| logrotate | 删除 `/etc/logrotate.d/health-ranlei-work` |
| 闸门 | `systemctl disable --now cert-expiry-check.timer`；删两个 unit + `daemon-reload`；删 `/opt/genesis-evidence/ops/cert-expiry-check.sh` |
| 患者回退 | **旧入口 `http://36.156.159.175:10007` 此刻仍可用**，门户仍绑 `0.0.0.0`（T6 未执行） |

## 边界声明

- 审核工作台与 Evidence API **没有**重新暴露：`10005` / `10006` 从外部仍为 `000`。
- 未给 80 端口加 catch-all，未恢复 `genesis-evidence.ranlei.work` 的 vhost 或审核台块。
- 未触碰 `api.qumall.qushiyun.com` 的证书或配置。
- 未合并任何 PR，未动分支保护，未在 #328 上打标签。
