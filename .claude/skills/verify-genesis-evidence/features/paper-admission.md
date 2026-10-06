# 研究身份确认与论文准入（paper admission）

## Sub-features

- 研究身份确认（研究是否为该主题下的有效研究）
- 人工准入 / 人工拒绝
- 人工覆盖筛选决定（override）
- 保存全文获取结果

## How to get to it (user POV)

连接 → 论文审核视图 → 选中队列中的一篇论文 → 右侧详情出现「研究身份确认与论文准入」。

```js
sessionStorage.setItem('reviewKey', KEY);
document.getElementById('connect').click();
document.querySelector('[data-view="review"]').click();
// then select a paper from the queue (#papers / queue list)
```

## Driving it with Playwright

Handles: `#admit`（准入）、`#admitStudyDesign`、`#identityConfirmed`、`#publicationRole`。

```js
// After selecting a paper
await page.locator('#identityConfirmed').check();
await page.locator('#admit').click();
```

## Gotchas

- **准入是一个有前置的状态机，不是一个按钮。** `研究身份确认` 必须先完成，否则
  准入按钮不会真正生效 —— 直接点 `#admit` 并断言列表变化，会得出错误结论。
- 论文队列为空时整个流程无从开始。**空队列是空数据库的正确渲染，不是准入功能被验证。**
  先经 API 造数据（见 `tests/test_review_api.py` 的调用序列）。
- 「人工覆盖」与「人工准入」是两条不同路径，断言时要说清走的是哪一条。
- 准入会改变该论文在后续所有环节的可见性 —— 复查时用**同一篇论文**继续，不要另选一篇。
