# Genesis Evidence

面向成人 40+ 人群的医学论文证据生产与个人体检报告解读系统。

当前阶段只有两条主线：

- 论文获取、全文处理、Claim 审核和正式知识卡发布。
- 多文件报告抽取、用户确认、确定性校验和正式知识卡匹配。

两条主线只通过 `published` 知识卡连接。患者可见结论必须引用正式知识卡，未发布内容不得出现在患者接口。

产品对外名称统一为“体检报告解读与健康风险提示”。

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
uv run python scripts/check_scope.py
uv run python scripts/check_schema.py
uv run python scripts/check_patient_copy.py
```

## Local services

The report portal reads its model configuration from server environment variables:

```bash
OPENAI_API_KEY=...
OPENAI_RESPONSES_URL=https://your-provider.example/v1/responses
OPENAI_REPORT_MODEL=gpt-5.6-sol
uv run genesis-evidence-portal
```

Use `OPENAI_RESPONSES_URL` when a proxy has a non-default path. `OPENAI_BASE_URL`
remains available for providers whose Responses endpoint is simply `<base>/responses`.

Long paper extraction runs outside the review request path:

```bash
ARK_API_KEY=...
uv run genesis-evidence-worker
```

The worker stores progress after extraction A, extraction B, and the consistency
check. The review workbench shows queued, running, failed, and completed jobs.
