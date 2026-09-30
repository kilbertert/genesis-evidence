# 体检报告 → 商城推荐闭环：端到端验收

**日期**：2026-09-30
**环境**：新加坡（`124.243.178.156`）；商城入口 `hstclub.com` / `h5.trendcloud.cc`
**服务**：health-flow（服务主机 `36`，`/opt/health-flow`），wheel sha256
`20ecb8ad…`（= `origin/main` `48278a1`，health-flow #120）
**租户**：`HST CLUB`（租户标识不写进本文，见「记录与边界」）

## 结论

**闭环通了。** 一张商城签发的票据 → health-flow 换到本地会话 → 检测页按健康风险取到
对应商品 → 「加入购物车」拿到一条落到该商品详情页的深链。四个负向用例全部按预期拒绝。

本次覆盖的方向是 **2 个**（见「覆盖范围的真相」）；其余 10 个方向取不到商品，原因是
**对应商品没有上架**，不是链路缺陷。

## 正向链路

| # | 步骤 | 实测结果 |
| --- | --- | --- |
| 1 | 商城侧签发 RS256 票据（用服务宿主自己的私钥） | 641 字节，`aud=health-flow` |
| 2 | `POST /api/auth/ticket` 兑换 | `201`，`subject = account:<租户>:<子>` |
| 3 | `GET /api/health/report/{id}/recommendations` | `200`，`items` 1 件、`reason: null` |
| 4 | `POST .../recommendations/{spu}/cart-link` | `200`，`reason: null`，URL 带 `spu_id` 与 `sig` |
| 5 | 深链落地 | 详情页 `200`；该页所需数据 `200`，价格与规格俱在 |

第 3 步的实际响应（截断）：

```json
{"items":[{"id":"…","name":"心腦通","image":"https://…jpg",
           "price_down":"480.0","price_up":"480.0","stock":null,"shop_id":"…"}],
 "reason":null}
```

第 4 步产出的深链形态：

```
https://hstclub.com/shopPackage/pages/goods/goods-detail/index
  ?detection_id=report-33&exp=…&id=<spu>&quantity=1&spu_id=<spu>&tenant_id=…&sig=…
```

## 负向用例

**三条是被拒绝，一条只是「空」**——两者不是一回事，不要合并成一句「全部拒绝」。

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| 无会话读报告商品 | 拒绝 | `401` ✅ |
| 同一张票据兑换第二次 | 拒绝 | `401` `票据已被使用` ✅ |
| 用本主体会话读**别人**的报告 | 拒绝 | `404` ✅ |
| 映射表外的 `condition_code` | **空结果**（不是拒绝） | 不查映射、`label_pairs_for` 返回 `()` → `no_label_data` ✅ |

最后一条的期望就是空：没有映射的健康方向**不去问商城**，也不退化成「把该租户全部商品
推给这个风险」。它没有 HTTP 错误码可报——调用方拿到的是 `200` + 空 `items` + 明确的
`reason`，这正是设计要的形态。

第三条是**存在性不泄漏**的形态：不属于你的报告返回 `404` 而不是 `403`，无法据此判断
某个 id 是否存在。

## 覆盖范围的真相（重要）

本次只在 **2 个健康方向**上真的取到了商品：

| `condition_code` | 取值 | 命中商品 |
| --- | --- | --- |
| `COND_HYPERTENSION_RISK` | `心血管功能评估` | `心腦通` |
| `COND_CHRONIC_CONSTIPATION` | `肠道健康评估` | `排毒消濕寶` |

其余 10 个方向的映射**已填、名字已在商城侧核实**，但取不到商品——因为该租户 12 件商品里
只有 4 件上架，且挂着标签的那 6 件里有 4 件是下架状态（见另份实测记录）。

**这与本次代码无关**：商城的按标签取货接口自己按「审核通过 + 已上架」过滤，下架商品
按设计就不返回。要扩大覆盖，运营侧要做的是**上架**，不是改映射。

「检测页上 12 个方向都有货」这句话**今天不成立**，本次验收不为它背书。

## 为验收造的夹具（需要清理）

真实报告的生产库里，报告归属是退役期的账号 uuid，与票据主体（`account:<租户>:<sub>`）
对不上——**没有一份现成报告能被本次的会话读到**。因此：

- 插入了一份**验收用报告**（`COND_HYPERTENSION_RISK`），归属为本次验收主体；
- 留下了 2 条**票据消费记录**与 1 条**主体记录**（`hst-acceptance-1`）。

三样都在服务宿主的 SQLite 库里，**是夹具不是数据**。清理方式**本仓还没有**——那句
「见 README 的验收夹具一节」是写这份记录时的预期，那节当时并不存在。现在就地给出
清理步骤，不指向一个不存在的地方：

**先停服务。** 服务在写库时 `cp` 出来的备份可能是撕裂的（SQLite 默认回滚日志模式下，
拷到一个「页写了一半」的瞬间），更糟的是**库还开着 WAL 时，已提交的事务可能只在 `-wal`
里、不在主文件里**——那样的备份用来回滚会丢数据。所以顺序是：

```bash
# 在服务主机 36 上，以 root 跑
systemctl stop health-flow                     # 1. 先停，保证没有在途写入
set -a; . /opt/health-flow/var/health-flow.env; set +a
cp /opt/health-flow/var/healthflow.db /opt/health-flow/var/healthflow.db.bak-$(date +%Y%m%dT%H%M%S)
```

停服务之后备份才是自洽的。清理完再 `systemctl start health-flow`。

```python
# 以服务身份跑（36 上是 health-flow）。SUB 是本次验收主体。
import os, sqlite3

c = sqlite3.connect(os.environ["DATABASE_URL"].replace("sqlite:///", ""))
cur = c.cursor()
TENANT = "<租户标识>"                      # 与报告、会话、主体三处都相关
SUB = f"account:{TENANT}:hst-acceptance-1"  # 报告与外键用的主体标识
for sql, args in (
    ("delete from medical_reports where owner_id = ?", (SUB,)),
    ("delete from user_sessions where account_id = ?", (SUB,)),
    # 票据那两张表按 (租户, 子标识) 定位——只按子标识会删到别的租户的同名行
    ("delete from ticket_subjects where tenant_id = ? and external_subject = ?",
     (TENANT, "hst-acceptance-1")),
    ("delete from ticket_redemptions where tenant_id = ? and subject = ?",
     (TENANT, "hst-acceptance-1")),
):
    cur.execute(sql, args)
    print(sql.split("from")[1].split("where")[0].strip(), "->", cur.rowcount, "行")
c.commit()
```

**两张票据表必须按 `(tenant_id, subject)` 两个条件删。** `ticket_subjects` 的身份约束
本就是 `(tenant_id, external_subject)`，`ticket_redemptions` 也有 `tenant_id` 列——
只给 `subject` 一个条件，别的租户若恰好也有一个叫 `hst-acceptance-1` 的主体，
它的记录会被一起删掉。清理脚本比它要删的那几行危险得多，条件要按表的身份键写全。

**顺序有讲究**：先删报告（它引用主体），再删会话与主体。票据消费记录与主体也可以留
——它们到 `exp` 自动失去意义，删它们只是把库擦干净。**不要**用不带条件的
`delete from medical_reports`：那会删掉真实患者的报告。

## 未验证

- **浏览器里的人眼确认**：本次全程 HTTP/API 层验证。H5 是 SPA，`200` 只证明页面与
  数据可达，不证明渲染结果、也不证明微信内置浏览器下的跳转行为（契约里已列为未验证项）。
- **登录续接**：深链形态是「跳到商品详情页」，未登录的商城会话不在本次射程内（契约里的
  方向 A/B 决策）。
- **归因**：`sig` 目前只担保「载荷由本平台签发且未被改动」，不担保「该商品与报告相关」。
- **按门店收窄**：未做，取货是租户级的。
- **其余 10 个方向**：见上节。

## 记录与边界

- 本次操作的服务主机是本编队登记在册的服务宿主（`36`）；商城侧入口是公司环境。
  商城侧的改动（新加坡商城的票据配置、应用绑定、映射表对应的标签）已在前序记录里逐条列出。
- 本文不含租户标识、SPU 标识与任何凭据：那些属于内部操作记录（`.scratch/`，库外）。
- 验收夹具留在生产库里，清理是独立的动作，不在本次范围内——写在这里是为了不让它变成
  「不知道哪来的数据」。
