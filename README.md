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
