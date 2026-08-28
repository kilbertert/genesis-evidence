# Genesis Evidence

面向成人 40+ 人群的医学论文证据生产与个人体检报告解读系统。

当前阶段只有两条主线：

- 论文获取、全文处理、Claim 审核和正式知识卡发布。
- 多文件报告抽取、用户确认、确定性校验和正式知识卡匹配。

两条主线只通过 `published` 知识卡连接。患者可见结论必须引用正式知识卡，未发布内容不得出现在患者接口。

产品对外名称统一为“体检报告解读与健康风险提示”。

## 线上访问

- 个人报告门户:https://genesis-evidence.ranlei.work
- 论文证据审核工作台:https://genesis-evidence-review.ranlei.work(首页填 `GENESIS_EVIDENCE_REVIEW_API_KEY` 作为 Bearer)

服务拓扑、环境变量和与旧 `genesis-health` 域名(`genesis-review` / `genesis-health`)
的区分,见 [docs/deployment.md](docs/deployment.md)。

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
uv run python scripts/check_scope.py
uv run python scripts/check_schema.py
uv run python scripts/check_patient_copy.py
uv run python scripts/check_product_catalog.py
```

## Product catalog

The product catalog is a candidate layer separate from published recommendations.
Backfill the legacy candidate pool once from the old literature database:

```bash
uv run python scripts/migrate_product_catalog.py --source-db ../genesis-health/var/literature/literature.db
```

Then verify the deterministic backfill contract:

```bash
uv run python scripts/check_product_catalog.py
```

## Local services

The Evidence API is a read-only published-card query service. It does not own
report uploads or a report extraction worker:

```bash
GENESIS_EVIDENCE_API_KEY=...
uv run genesis-evidence-api
```

Long paper extraction runs outside the review request path:

```bash
ARK_API_KEY=...
uv run genesis-evidence-worker
```

The worker stores progress after extraction A, extraction B, and the consistency
check. The review workbench shows queued, running, failed, and completed jobs.
