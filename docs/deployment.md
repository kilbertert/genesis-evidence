# 部署与访问

genesis-evidence 运行在**服务宿主**上(不是开发机)。本文说明线上服务的访问方式与拓扑。
部署资产在 `ops/service-host/`;面向服务宿主的安装步骤与验证方法见该目录下的 README。

## 线上访问

> **本文件描述的是目标形态,不是此刻的线上状态。** 下表里的入口已在仓库中定义并通过
> 评审,但**尚未部署到服务宿主**;合并这次改动时,线上仍然是下面的"现状"。
> 切换由 #331(T3 canary)与 #334(T6 收敛)执行,完成之前旧入口是回退路径。
>
> | | 入口 | 服务绑定 |
> | --- | --- | --- |
> | **现状(合并时)** | `http://<服务宿主>:10007` | 门户绑 `0.0.0.0`,cookie 不带 `Secure` |
> | **目标(部署后)** | `https://health.ranlei.work` | 门户只绑 `127.0.0.1`,cookie 带 `Secure`,TLS 由宿主 web 服务器终结 |

| 服务 | 入口 | 端口 | 说明 |
|---|---|---|---|
| 个人报告门户 | `https://health.ranlei.work` | 443 → `127.0.0.1:10007` | Health-Flow 体检报告解读与健康风险提示(主线 B);**唯一公开入口**,TLS 由宿主 web 服务器终结 |
| 论文证据审核工作台 | `127.0.0.1:10006` | 10006 | 论文采集/抽取/审核/知识卡(主线 A);**只绑环回,收回已于 2026-09-28 执行** |
| Evidence API | `127.0.0.1:10005` | 10005 | 只读已发布证据 API;**只绑环回,不对外** |

用户端由 Health-Flow 提供页面与报告 API,并在用户确认指标后调用**本机**
`127.0.0.1:10005/api/evidence/matches`;该 Evidence API 是内部边,刻意不暴露。

**入口的形态**(决策见 ADR 0019 与 ADR 0020,配置文件 `ops/service-host/nginx/health.ranlei.work.conf`):

> **2026-10-27 起,这个入口服务的是 health-flow 的 API,不再是一个患者页面**(ADR 0020)。
> 患者界面已并入商城 H5(PRD kilbertert/Health-Flow#266)。下表中「个人报告门户」那一行的
> **传输形态仍然有效**(TLS 在宿主终结、只绑 `127.0.0.1`),**载荷已改变**:`/` 不再提供患者
> 页面。上线切换窗口内两者并存,切换完成后本节的验收项要从「页面能打开」改为「API 可达」。
>
> **验收脚本尚未随之修改**:`ops/service-host/acceptance/e2e-acceptance.sh` 仍断言 `/` 返回
> 患者页面(含「健康流」标题)。它是**切换前**的验收,**现在仍然正确**;新模块上线时需要一条
> 只验 API 的验收项,那是与该入口切换同一个窗口的工作,不在本次文档改动内。

- 患者门户只绑 `127.0.0.1`;唯一的对外入口是宿主 web 服务器上的那一个 `server` 块。
  **应用改动不再等于暴露改动。**
- `X-Forwarded-For` 用 `$remote_addr` **覆写**而不是追加;HSTS 一年,不带
  `includeSubDomains`、不带 `preload`。
- 该入口声明自己的 `access_log`,格式用 **`$uri`** —— 商城入口页以 `/?ticket=…`
  跳转,那张票是凭据,不能永久写进磁盘。轮转由
  `ops/service-host/logrotate/health.ranlei.work` 负责,**只覆盖我们那两个文件**。
- 证书由 **Let's Encrypt** 签发(ECDSA,webroot),续期后的"拷证书 + reload"由
  `ops/service-host/cert-deploy-hook.sh` 完成,按 **lineage** 注册,不在全局钩子目录。
  **`certbot renew --dry-run` 不执行 deploy hook**(certbot 2.9.0 实测),不能拿它当钩子可用的证据。
- 证书到期闸门 `ops/service-host/cert-expiry-check.sh` 每天跑一次:任何一张我们负责的
  证书剩余 ≤ 21 天时 unit 进入 `failed`。**在还没有告警渠道之前,`systemctl --failed`
  是这台机上唯一会被看见的信号。** 它只读。

**过渡状态与回退**:`health.ranlei.work` 是**过渡域名**,依赖个人注册商与个人 Cloudflare
账户;迁到公司域名是单独一张阻塞票,不是本次的终态。回退路径是旧入口
`http://<服务宿主>:10007`(在收敛完成之前仍在运行),以及按 DNS 记录 **id** 删除那条 `A` 记录。

个人报告门户以 **账号会话** 为患者访问边界(`REPORT_ACCOUNT_REQUIRED=true`)。RFC 7617 Basic Auth
只是可选的操作者兼容闸门(`HEALTHFLOW_BASIC_AUTH_ENABLED`,默认关闭),不是患者访问路径;启用时凭据
仅存在 Health-Flow 的私有 `var/health-flow.env` 中。

## 审核工作台访问

工作台首页无鉴权,但所有 `/api/review/*` 接口要求 Bearer 密钥。打开工作台入口
(见上表),在页面顶部的「审核密钥」输入框填入 `GENESIS_EVIDENCE_REVIEW_API_KEY`
(见服务宿主上的 `var/review.env`),页面会把密钥存在 sessionStorage 并随每次请求发送。

审核人身份来自服务端环境变量 `GENESIS_EVIDENCE_REVIEWER_ID`(当前为 `ranlei`),
不在浏览器侧配置。

## 服务清单(服务宿主 systemd)

服务宿主上每个项目有**自己的**系统身份,以系统级 unit 托管:

| unit | 作用 |
|---|---|
| `genesis-evidence-portal.service` | 只读 Evidence API(FastAPI,10005);不承载报告上传/解析 |
| `genesis-evidence-review.service` | 审核工作台(FastAPI,10006) |
| `health-flow.service` | Health-Flow 用户端 + 报告 API(FastAPI + React,10007) |
| `health-flow-report-worker.service` | Health-Flow 持久化报告解析 worker,消费报告抽取队列 |
| `cert-expiry-check.timer` / `.service` | 证书到期闸门(每天一次);失败即进入 `failed`,是这台机上唯一的信号 |

论文抽取 worker **尚未部署到服务宿主**:迁移时按既定决策保持停用(单主题低速验收),
恢复抽取是另一次单独的低速验收。开发机上的 `genesis-evidence-worker.service` 已随之退役。

服务宿主上的 unit 是**系统级** unit,以各项目自己的系统身份运行(不是交互式账号的 user unit):

```bash
systemctl status genesis-evidence-portal genesis-evidence-review health-flow
systemctl restart genesis-evidence-review   # 改了 env 后重启
```

**证据服务与 Health-Flow 不共享身份**:两者各自一个系统账号,不共享任何组,因此谁都读不到对方的
凭据文件。这是刻意的 —— 一个项目的缺陷不该能读到另一个项目的密钥。

## 环境变量

三个私有 env 文件,位于服务宿主的项目根下,不提交 git(见 `.gitignore`):

- `var/review.env` — 审核工作台 + worker 共用:
  `GENESIS_EVIDENCE_DATABASE`、`GENESIS_EVIDENCE_REVIEW_HOST/PORT`、
  `GENESIS_EVIDENCE_REVIEW_API_KEY`(工作台 Bearer)、`PAPER_AI_API_KEY_FILE`
  (或 `PAPER_AI_API_KEY`)、`PAPER_AI_BASE_URL`、`PAPER_AI_MODEL`、
  `PAPER_AI_TIMEOUT_SECONDS`、`PAPER_AI_MAX_TOKENS`(抽取输出预算)。
  旧的 `ARK_*` 变量名仍被兼容读取,但不再是配置来源。
- `var/portal.env` — Evidence API:
  `GENESIS_EVIDENCE_PORTAL_HOST/PORT`、`GENESIS_EVIDENCE_API_KEY` 等。
- `health-flow/var/health-flow.env` — Health-Flow:
  `GENESIS_EVIDENCE_API_URL`、同值的 `GENESIS_EVIDENCE_API_KEY` 等。Health-Flow 只在用户确认指标后调用
  `POST /api/evidence/matches`，并通过 `X-Genesis-Evidence-Key` 认证；上传接口先持久化并返回 `202 processing`，
  由 `health-flow-report-worker.service`(`app/service/report_worker.py`)在持久化队列上完成解析，
  状态进入 `pending_confirmation` 后前端通过带访问令牌的短请求轮询，不依赖反向代理长连接。

数据:`var/genesis-evidence.sqlite3`(SQLite)。Evidence API 不读取报告对象存储。

## 与 genesis-health 的关系

`genesis-health` 是阶段二之前的旧仓库,已按决策冻结(不再开发)。它仍占用旧域名:

- `https://genesis-health.ranlei.work` — 旧个人健康门户
- `https://genesis-review.ranlei.work` — 旧证据审核工作台

**这四个域名现在全部不可用**,不要用其中任何一个:旧的两个已于 2026-08-13 停用,
`genesis-evidence*` 两个也随 2026-09-22 的迁移退役。**当前入口是本文开头的
`https://health.ranlei.work`**;它是过渡域名,迁到公司域名后以新域名为准。

旧 genesis-health 的 6 个 user 服务(`genesis-health-portal`、
`genesis-health-private-portal`、`genesis-review-api`、`genesis-health-frp`、
`genesis-health-private-tunnel`、`genesis-health-public-tunnel`)已于
2026-08-13 全部 `stop + disable`,旧域名返回 404;代码与数据冻结在
`/home/claude/Projects/genesis-health`,未删除。需要临时恢复时:

```bash
systemctl --user enable --now genesis-health-frp genesis-health-portal genesis-review-api
```
