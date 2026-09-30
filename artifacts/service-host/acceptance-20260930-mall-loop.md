# 体检报告 → 商城推荐闭环：端到端验收（2026-09-30，第二版）

**日期**：2026-09-30
**环境**：新加坡（`124.243.178.156`）；商城入口 `hstclub.com`
**服务**：health-flow（服务主机 `36`，`/opt/health-flow`），wheel sha256 `3a1dd507…`
（= `origin/main` `0acc9cc`）
**租户**：`HST CLUB`（租户标识不写进本文，见「记录与边界」）

> 本文取代同日的第一版。第一版只做了 API 层（它自己的「未验证」一节把浏览器确认列为
> 未做）；本版补上了真实浏览器全链路，并记录了一次**用真实报告跑通**的完整闭环。

## 结论

**闭环通了，而且这次是拿一份真实的体检报告跑通的**——不是造出来的夹具。

链路：商城签发票据 → health-flow 建立会话 → **上传真实报告图片** → 视觉模型抽取指标 →
用户确认 → 证据服务按指标匹配出健康风险 → 检测页按该风险取到商城商品 →
点「加入购物车」落到那件商品的商城详情页（页面渲染出商品与价格）。

四个负向用例按预期拒绝。

## 正向链路（真实报告）

| # | 步骤 | 实测结果 |
| --- | --- | --- |
| 1 | 商城签发 RS256 票据 | 641 字节，`aud=health-flow` |
| 2 | `POST /api/auth/ticket` 兑换 | `201`，主体 `account:<租户>:<子>` |
| 3 | 上传**真实**化验单（5 页 JPG） | `202 processing` |
| 4 | worker 抽取 | `pending_confirmation`，**72 条指标**，其中 10 条自动带上了 `metric_code` |
| 5 | 用户确认全部指标 | `200`，状态转 `assessed` |
| 6 | 证据服务匹配 | findings **1 条**：`COND_DYSLIPIDEMIA`（血脂异常） |
| 7 | 检测页推荐 | `200`，`items` 1 件（`心腦通`，¥480），`reason: null` |
| 8 | 加购深链 | `200`，URL 带 `spu_id` 与 `sig` |
| 9 | **浏览器全程**（Playwright，线上门户，不打桩） | 见下 |

第 9 步的浏览器观察：

| 观察 | 结果 |
| --- | --- |
| 带票据进入，地址栏 `ticket` | 已清掉 |
| **点击前**浏览器对商城域名的请求 | **0 次**（前端没有直连商城） |
| 检测页渲染 | 「推荐商品 / 心腦通 / ¥480.0 / 库存未标注 / 加入购物车」 |
| 点「加入购物车」 | 落到 `https://hstclub.com/shopPackage/pages/goods/goods-detail/index?…` |
| 落地页 | 渲染出「**心腦通 / 促進血液循環 改善手腳冰冷 / ￥480 / 库存100件**」与加购按钮 |

## 负向用例

**上面这一版的四条全部是「拒绝」类的**（另有「返回空结果」一类，见下方说明）。

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| 无票据进入门户 | 提示回商城入口 | 提示正确，且**无前端运行时错误** |
| 篡改票据末 6 位 | 拒绝 | 被拒，回到入口提示 |
| 无会话读报告商品（API） | `401` | `401` |
| 用本主体读**别人**的报告（API） | 拒绝 | `404`（不是 `403`——存在性不泄漏） |

**「拒绝」之外还有一类：「取不到货」是空结果，不是错误。** 健康方向在商城侧没有
「已上架且已挂标签」的商品时，接口返回 `200` + 空 `items` + `reason: no_label_data`。
它**没有 HTTP 错误码可报**——调用方拿到的是一个明确的空，而不是失败。两类不要合并
成一句「全部拒绝」。

## 覆盖范围的真相

**只有 2 个健康方向能在真实报告上跑通**——不是因为链路，是因为**货**：

| `condition_code` | 商城取值 | 命中商品 |
| --- | --- | --- |
| `COND_DYSLIPIDEMIA` | 血脂异常风险评估 | `心腦通` |
| `COND_CHRONIC_CONSTIPATION` | 肠道健康评估 | `排毒消濕寶` |

其余方向取不到货的成因分两类，**修法不同**：

1. **商品没上架**：挂着标签的商品处于下架状态，商城的按标签取货接口自己按「审核通过
   + 已上架」过滤，按设计不返回。**修法是上架**，不是改映射。
2. **没有商品挂那个标签**：该方向目前没有任何商品标注。**修法是挂标签**。

同一份报告上**不能**同时验证多个方向——一份报告的 findings 由它的指标决定。
要扩大覆盖，需要**更多不同指标的体检报告**，以及**更多挂了对标签的在售商品**。

## 为验收造/改的东西（需要清理或知悉）

- **上传了 3 份验收用报告**（`35`/`36`/`38` 号，另有 `37`），归属为验收主体。
  其中 `38` 是用**真实化验单**跑通全链路的那一份。
- **留下了票据消费记录与一条主体记录**（`hst-acceptance-1`）。
- **商城侧**：验收过程中运营为 `心腦通` 追加了「血脂异常风险评估」标签，并把
  `肝爽通` 上架。这两条是**商城侧的真实业务改动**，验完**没有回滚**——它们是可持续
  使用的配置，不是夹具。

清理方式见本节末尾的命令。**先停服务再备份**——服务在写库时 `cp` 出来的备份可能
撕裂，而库开 WAL 时已提交的事务可能只在 `-wal` 里。

```bash
# 在服务主机 36 上以 root 跑。整段粘进一个 shell 即可。
set -euo pipefail

systemctl stop health-flow
cp /opt/health-flow/var/healthflow.db \
   /opt/health-flow/var/healthflow.db.bak-$(date +%Y%m%dT%H%M%S)

# DATABASE_URL 必须**在这个 shell 里重新 source**：环境文件属主是服务身份、root 读不到，
# 且上一步的导出不会跨 runuser 传递。漏掉就 KeyError。
runuser -u health-flow -- bash -lc '
  set -euo pipefail
  set -a; . /opt/health-flow/var/health-flow.env; set +a
  /opt/health-flow/.venv/bin/python - <<PY
import os, sqlite3

c = sqlite3.connect(os.environ["DATABASE_URL"].replace("sqlite:///", ""))
cur = c.cursor()
TENANT = os.environ["MALL_WEBAPI_TENANT_ID"]
SUB = f"account:{TENANT}:hst-acceptance-1"
for sql, args in (
    ("delete from medical_reports where owner_id = ?", (SUB,)),
    ("delete from user_sessions where account_id = ?", (SUB,)),
    # 票据两张表按 (租户, 子标识) 两个条件删——只给子标识会删到别的租户的同名主体
    ("delete from ticket_subjects where tenant_id = ? and external_subject = ?",
     (TENANT, "hst-acceptance-1")),
    ("delete from ticket_redemptions where tenant_id = ? and subject = ?",
     (TENANT, "hst-acceptance-1")),
):
    cur.execute(sql, args)
    print(sql.split("from")[1].split("where")[0].strip(), "->", cur.rowcount, "行")
c.commit()
PY
' && systemctl start health-flow
```

**执行前必须核对主体归属。** 上面按**主体标识**删，不按报告编号筛——如果验收之后有人
复用同一个主体继续上传，那些新报告会一起被删掉。跑之前先看一眼：

```sql
select id, created_at from medical_reports where owner_id = '<上面那个 SUB>';
```

确认待删的就是本次那几份（`35`/`36`/`37`/`38` 号，创建时间都在 2026-09-30 这次验收
的时间窗内）再执行。**如果主体被复用了，改成按报告编号删**。

`delete` 一律带 `where`；不带条件的 `delete from medical_reports` 会删掉真实患者的报告。

## 本轮一并修掉的两个缺陷

| 现象 | 根因 | 修法 |
| --- | --- | --- |
| 报告识别报 `VLM 未返回 JSON` | 抽取的输出预算用类默认 2048，被截断在 JSON 中间；**且 Responses 路径根本没把预算发给服务端** | 做成配置项（默认 16384）**并显式带 `max_output_tokens`**（health-flow #121） |
| 加购落地页渲染不出商品 | 商城页面读的参数名与契约里的 `spu_id` 不一致 | 深链**两个都带、同值**（genesis #192/#193） |

第二条**没有做实**：见「未验证」——「哪个参数名必需」至今未确认。

## 未验证

- **商城 H5 点「加入购物车」之后页面向服务端发的请求形状**：未验证。那需要在页面里
  真的点一次加购，会改变对方的生产数据。本条到此为止。
- **深链里 `id` 与 `spu_id` 哪个是必需的**：未确认。对照实验显示**两个参数单独出现时
  页面都能渲染出商品**，所以此前的「只带 `spu_id` 取不到」不成立，已在契约里撤回。
- **微信内置浏览器中的跳转行为**：未验证。
- **归因**：`sig` 只担保「载荷由本平台签发且未被改动」，不担保「该商品与报告相关」。
- **按门店收窄**：未做，取货是租户级的。
- **浏览器里的人眼检查**：Playwright 断言的是渲染出的文本，不是人眼观感（字体、排版、
  真机）。

## 记录与边界

- 服务主机是本编队登记在册的服务宿主（`36`）；商城侧是公司环境，其侧的改动（票据配置、
  应用绑定、标签与上架）在前序记录里逐条列出。
- 本文不含租户标识、SPU 标识与任何凭据：那些属于内部操作记录（`.scratch/`，库外）。
