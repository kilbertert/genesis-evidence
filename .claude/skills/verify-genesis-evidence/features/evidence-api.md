# 只读已发布证据 API

## Sub-features

- `GET /health` — 存活
- `GET /api/metrics` — 已发布证据的指标
- `GET /` — 服务根

这个 API 是 health-flow 报告门户读取"已发布证据"的**内部边**，不作为用户域暴露。

## How to get to it (user POV)

不是给终端用户的界面；消费者是 health-flow 服务端。

```bash
uv run python .claude/skills/verify-genesis-evidence/scripts/observe.py \
    --service api --out var/verify-evidence
```

## Driving it with Playwright

不涉及浏览器。用 HTTP：

```bash
curl -s "http://127.0.0.1:8125/health"
curl -s "http://127.0.0.1:8125/api/metrics"
```

## Gotchas

- 只读：这个服务**不应**接受写入。若某次改动让它写了状态，那是契约破坏，应作为
  缺陷报告而不是"验证通过"。
- 返回内容的形状是 health-flow 读取的契约。改动响应结构要同时想到 health-flow 侧 ——
  只在本仓断言"接口 200"不能证明消费方还能解析。
- `scripts/check_patient_copy.py` 会对患者可见文案做禁用词检查；若你改的是患者可见
  证据文案，先跑它，别等 CI。
