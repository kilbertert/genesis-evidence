# 部署与访问

genesis-evidence 是阶段二重建项目,与冻结的 `genesis-health` 并排运行。本文说明线上服务的 URL、访问方式和拓扑。

## 线上 URL

| 服务 | 域名 | 本地端口 | 说明 |
|---|---|---|---|
| 个人报告门户 | `https://genesis-evidence.ranlei.work` | 127.0.0.1:8125 | 体检报告解读与健康风险提示(主线 B) |
| 论文证据审核工作台 | `https://genesis-evidence-review.ranlei.work` | 127.0.0.1:8126 | 论文采集/抽取/审核/知识卡(主线 A) |

两个域名由 `ops/frp/genesis-evidence.toml` 通过 subdomain 暴露,Nginx
(`/etc/nginx/sites-enabled/frp-demo`)把 `*.ranlei.work` 的 443 转发到本地
FRP 服务(127.0.0.1:8188),再由 FRP 隧道回落到对应本地端口。

## 审核工作台访问

工作台首页无鉴权,但所有 `/api/review/*` 接口要求 Bearer 密钥。打开
`https://genesis-evidence-review.ranlei.work`,在页面顶部的「审核密钥」输入框填入
`GENESIS_EVIDENCE_REVIEW_API_KEY`(见 `var/review.env`),页面会把密钥存在
sessionStorage 并随每次请求发送。

审核人身份来自服务端环境变量 `GENESIS_EVIDENCE_REVIEWER_ID`(当前为 `ranlei`),
不在浏览器侧配置。

## 服务清单(user systemd)

| unit | 作用 |
|---|---|
| `genesis-evidence-portal.service` | 报告门户(FastAPI,8125) |
| `genesis-evidence-review.service` | 审核工作台(FastAPI,8126) |
| `genesis-evidence-worker.service` | 论文抽取后台 worker,逐条消费 `paper_extraction_jobs` |
| `genesis-evidence-frp.service` | FRP 隧道,暴露上面两个 HTTP 服务 |

运维命令(均以 `claude` 用户):

```bash
systemctl --user status genesis-evidence-{portal,review,worker,frp}
systemctl --user restart genesis-evidence-worker   # 改了 env 后重启 worker
```

## 环境变量

两个私有 env 文件,不提交 git(见 `.gitignore`):

- `var/review.env` — 审核工作台 + worker 共用:
  `GENESIS_EVIDENCE_DATABASE`、`GENESIS_EVIDENCE_REVIEW_HOST/PORT`、
  `GENESIS_EVIDENCE_REVIEW_API_KEY`(工作台 Bearer)、`ARK_API_KEY`、
  `ARK_MAX_TOKENS`(抽取输出预算)。
- `var/portal.env` — 报告门户:
  `GENESIS_EVIDENCE_PORTAL_HOST/PORT`、`OPENAI_API_KEY`、`OPENAI_RESPONSES_URL`、
  `OPENAI_REPORT_MODEL` 等。

数据:`var/genesis-evidence.sqlite3`(SQLite)、`var/objects`(内容寻址全文)。

## 与 genesis-health 的关系

`genesis-health` 是阶段二之前的旧仓库,已按决策冻结(不再开发)。它仍占用旧域名:

- `https://genesis-health.ranlei.work` — 旧个人健康门户
- `https://genesis-review.ranlei.work` — 旧证据审核工作台

**访问新版一律用 `genesis-evidence*` 域名,不要再用 `genesis-review` / `genesis-health`
这两个旧域名**,以免混淆。

旧 genesis-health 的 6 个 user 服务(`genesis-health-portal`、
`genesis-health-private-portal`、`genesis-review-api`、`genesis-health-frp`、
`genesis-health-private-tunnel`、`genesis-health-public-tunnel`)已于
2026-08-13 全部 `stop + disable`,旧域名返回 404;代码与数据冻结在
`/home/claude/Projects/genesis-health`,未删除。需要临时恢复时:

```bash
systemctl --user enable --now genesis-health-frp genesis-health-portal genesis-review-api
```
