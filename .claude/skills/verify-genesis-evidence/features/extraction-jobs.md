# 抽取任务（extraction jobs）与失败重试

## Sub-features

- 抽取任务列表与状态
- 重试失败阶段
- AI 处理全部待办 / AI 自动完成当前论文
- 两次独立同模型抽取差异裁决

## How to get to it (user POV)

连接 → 论文审核视图 → 「抽取任务」（`#jobs`）。

## Driving it with Playwright

```js
await page.locator('[data-view="review"]').click();
await page.locator('#jobs');            // extraction job list
await page.getByRole('button', { name: '重试' }).click();
```

## Gotchas

- **抽取会调用外部模型**（`PAPER_AI_BASE_URL` / `PAPER_AI_MODEL`）。这是真实的生产
  边界：不要 mock 它并声称该链路已验证。要么让它真跑，要么明确写出你替换了它的输出。
- 「重试失败阶段」只在**确实有失败阶段**时才有意义。造出一个失败态是前提，否则
  断言的是"没有失败可重试"，那是空态。
- 「两次独立同模型抽取差异裁决」依赖两次抽取都完成；只跑一次不会出现差异面板。
- `#autoAll`（AI 处理全部待办）会批量改状态 —— 在隔离库上跑，别在真实库上试。
