# Result 与 Claim 审核 / Evidence Profile 与知识卡

## Sub-features

- Evidence Profile（`profileTarget` / `profileCertainty` / `profileRationale`）
- Result 与 Claim 逐条审核（人工覆盖并批准 / 人工拒绝）
- 知识卡创建与版本（`createCard` / `cardTopic` / `cardCondition` / `cardBody`）
- 状态转换与执行依据（审计轨迹）

## How to get to it (user POV)

连接 → 论文审核视图 → 选中一篇**已准入**的论文 → 详情中的
「Evidence Profile 与知识卡」「Result 与 Claim 审核」。

## Driving it with Playwright

```js
await page.locator('#profileTarget').fill('…');
await page.locator('#profileCertainty').selectOption(…);
await page.getByRole('button', { name: '人工覆盖并批准' }).click();
await page.locator('#cardBody').fill('…');
await page.getByRole('button', { name: '人工创建草稿' }).click();
```

## Gotchas

- `#profileCertainty` 的选项与它旁边那句可见性说明**不再写死在页面里**：页面从
  `GET /api/review/strength-vocabulary` 取词表与文案（#238）。断言"选择器里有哪些
  确定性等级"时，比对的是这个端点的返回值，而不是页面里的字面量；若端点返回空，
  选择器会是**空的**——那是端点/认证的问题，不是"等级只有一个"。
- **论文必须先准入**，否则这一段不出现。审核链是有顺序的：准入 → 抽取 → Profile → Claim → 卡片。
- 卡片有**版本**（`#cardVersion`）。断言"卡片已创建"时要说明是哪个版本 —— 覆盖会新
  增版本而不是就地改写，只断言存在会漏掉版本回归。
- 「状态转换与执行依据」是审计轨迹，是**副作用**的观测点：审核动作应在这里留下记录。
  只截图最终界面而不查这条轨迹，等于没验证副作用。
- 「人工覆盖」（override）与常规批准是不同路径，结论不同；断言要指明走了哪条。
