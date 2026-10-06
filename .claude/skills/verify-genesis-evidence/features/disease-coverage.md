# 疾病框架覆盖矩阵与知识卡库

## Sub-features

- 疾病框架覆盖矩阵（`#coverage`）
- 疾病知识库筛选（`aria-label="疾病知识库筛选"`）
- 知识卡列表与已发布状态
- 主体筛选台账（`ledger` / `ledger/reconcile`）

## How to get to it (user POV)

连接 → 切换到「疾病知识库」视图（`data-view="disease"`）。

```js
document.querySelector('[data-view="disease"]').click();
```

## Driving it with Playwright

```js
await page.locator('[data-view="disease"]').click();
await expect(page.locator('#coverage')).toBeVisible();
await expect(page.locator('.disease-summary, #diseaseSummary')).toBeVisible();
```

## Gotchas

- 这是**只读视图**（矩阵、卡片列表、台账）。在这里"验证"一个写入功能是走错了视图。
- 覆盖矩阵在空库上是空的 —— 空矩阵是空数据库的正确渲染。矩阵的真实形状依赖已发布
  卡片与疾病框架条目同时存在。
- 「已发布」是知识卡可见性的分界：草稿不出现在患者侧。断言覆盖矩阵时先确认卡片是
  `published`，否则你测的是"草稿没显示"，那是另一条规则。
