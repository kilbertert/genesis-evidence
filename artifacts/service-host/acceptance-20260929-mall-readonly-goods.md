# 商城商品只读端点在 41 环境的开通与验收

**日期**：2026-09-29
**范围**：`qumall` `cloud-mall-api` 的第三方只读商品端点 `POST /webapi/goods/read`（本仓库侧跟踪为 #167）
**环境**：41 环境（`47.97.160.153`，商城 dev，公网域名 `lkf.h5.mall.qushiyun.com`）
**测试租户**：东宸药业 `tenant-id=1578664130444005376`

## 结论

端点 **已可用**：以注册的测试应用身份 + 东宸租户调用，返回该租户的在售商品，
返回项字段与契约一致，且**四个负向用例全部按预期拒绝**。

任务分工上，这一步由本仓库执行是**经运营明确授权的一次公司侧改动**：
Nacos 地址与凭据由运营提供并授权，落点经确认后由本仓库写入。这不是本编队的主机，
因此记录在此以便追溯（见「记录与边界」）。

## 阻塞的根因（第一性原理）

`WebApi接口未开放,请联系平台管理员`：对照 `ApplicationInterceptor` 的判定顺序，
它**只有一个来源** —— `MallConfigProperties.getApplications()` 为空。

调这个场景真正要回答的是三个问题，逐个落到证据上：

1. **配置在哪里** —— `MallConfigProperties` 是 `@ConfigurationProperties(prefix = "base.mall")`
   + `@RefreshScope`；`cloud-mall-api` 的 `bootstrap.yml` 通过 Nacos
   `shared-configs[0]: application-${spring.profiles.active}.yml`（`refresh: true`）导入。
   所以 `base.mall` 来自 **Nacos**，不是数据库、不是仓库内资源文件。
2. **能不能发** —— 该 Nacos 的配置发布需要权限；本编队此前没有登记任何写权限，
   因此这一条**不能自行推断**，是运营授权后才具备的。
3. **发到哪个文档** —— 决定放 `application-dev.yml` 还是 `cloud-mall-api-dev.yml`。
   实测 `application-dev.yml` 的 `base.mall` 只有
   `logisticsKey / notifyHost / userDefaultAvatar / avatars / customer / tenantId`，
   **没有 `applications`**，与商城侧的说法一致。

### 为什么落在服务专属文档

实际写入 `cloud-mall-api-dev.yml`（其 `base` 下补一个 `mall:` 子树），而不是
`application-dev.yml`。两条实测依据：

- **不会被商城后台抹掉**：`MallConfigController.updateById`（`PUT /mallconfig`）会**重建整个
  `base.mall` 节点**，只回写 `notifyHost / logisticsKey / userDefaultAvatar / avatars / customer`
  五个键。写在 `application-dev.yml` 的 `applications` 会在下一次后台保存配置时被删除；
  写在服务专属文档则不受该接口影响。
- **密钥不泄漏**：`MallConfigController.getBy` 只对 `logisticsKey` 与 `customer` 做脱敏，
  **不脱敏 `applications[].key`**。写在 `application-dev.yml` 会让应用密钥随该接口返回；
  写在服务专属文档则不经过它。

### 一个必须知道的耦合（本轮实测发现）

`applications` 在**同一份配置里被两处消费**，不是纯鉴权开关：

| 消费方 | 作用 |
| --- | --- |
| `ApplicationInterceptor` | `@WebApi` 的应用白名单与租户绑定（我们要的） |
| `TaisErpOrderPaySuccessListener` | 泰思 ERP 订单同步：**本租户每笔支付成功回调都会向 `taisi.orderNotifyHost` 外发订单与用户信息** |

也就是说，为东宸登记应用的同时，等于把东宸加进了泰思 ERP 的推送范围。
41 是 dev、且改动可逆；该副作用已知并接受，在此记录，**不是**无意中的副作用。

## 落地的配置

`cloud-mall-api-dev.yml` 的 `base` 下新增（值为本机生成的测试应用，密钥不入库、不在本文）：

```yaml
base:
  # …原有的 system:/tenant: 子树未改动…
  mall:
    applications:
      - appId: <注册的应用标识>
        tenantId: "1578664130444005376"   # 东宸药业
        key: <应用密钥>
        signType: MD5
```

写入方式为**文本级插入**（定位 `base:` 块末尾、插入 `mall:` 子树），未做 YAML 重新序列化，
因此其余 4374 字节**逐字未变**；写后回读校验：`applications` 存在且 appId/tenantId 匹配，
顶层键集合与写入前一致，`base` 键集合为 `mall/system/tenant`。

应用密钥存放在**本机密钥文件**（仓库内 `var/`，已被 `.gitignore` 覆盖，权限 `0600`），
只用于签名，不写入任何受版本控制的文件、不打印到终端。

## 验收

签名算法取自 `WebApiSignUtils`：整个 body 去掉 `sign` 后按键名升序拼
`k=v&`（值 trim、跳过空值），末尾接 `appSecret=<key>`，结果为
`MD5( MD5(串) + key )`，两次均为 UTF-8、大写十六进制。签名在**本机离线计算**。

### 正向

```
POST /mallapi/webapi/goods/read
HTTP 200 | code=0 | items=5
字段：id, image, name, priceDown, priceUp, shopId, stock
```

字段与 `WebApiReadGoodsItem` 契约一致；**未**出现 `GoodsSpu` 实体的成本价/供应商价等内部字段。

### 负向（全部按预期拒绝）

| 场景 | 响应 |
| --- | --- |
| `mall-app-id` 未注册 | `应用ID无效` |
| 签名错误 | `签名错误` |
| 缺少 `tenant-id` | `Header缺少参数:tenant-id` |
| 缺少 `mall-app-id` | `Header缺少参数:mall-app-id` |
| 租户与应用的绑定不匹配 | `应用ID与与租户ID不匹配` |
| **签名后篡改 body**（改 `size`） | `签名错误` |

最后一条说明签名覆盖**整个请求体**，不是只签少数字段。

### 租户边界交叉核对

同一租户经既有 C 端分页接口（`tenant-id` + `client-type: H5`）取 50 条，
新端点返回的 5 个 `id` **全部落在**该窗口内 —— 租户过滤生效，返回的确是该租户的商品。

### 分页上限

请求 `size=999` 实际返回 **100** 条，与源码的 `MAX_PAGE_SIZE=100` 一致。

## 未做的事

- **未验证按标签过滤**：本端点只按租户/门店取在售商品，不涉及标签。
  `docs/condition-to-mall-tag.md` 的 12 行映射**仍未在真实租户上核对**（#170），
  需要商城侧给出东宸实际存在的 `(标签名, 标签值)`，或在商城后台补建后再核对。
- **未验证内网直连**：全部验证走公网域名 `lkf.h5.mall.qushiyun.com`；
  服务端代理应走的地址由部署侧决定。
- **未验证 `cloud-member`**（#168）：该服务在三个环境中均未部署运行。

## 记录与边界

- 本次修改的是**公司运营的 dev 环境**（41）上 `cloud-mall-api` 的一份 Nacos 配置。
  该主机在私有清单中登记为 `service-host / owner=qushiyun`，本编队的声明只覆盖 36；
  本次操作由运营**显式提供凭据并授权**，不属于本编队的常规可达范围。
- 改动范围：单文档、单次插入、可回滚（删掉 `base.mall.applications` 即恢复原状）。
- 未触碰其他任何服务、任何其他配置、任何数据库。
- 本轮未调整本地接收侧代码；端点的消费方（health-flow 的服务端代理）是后续工作。
