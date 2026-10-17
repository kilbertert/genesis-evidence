---
title: literature-ingestion
desc: 主题采集——连接器检索、全文获取与落库边界
hue: 150
code:
  - src/genesis_evidence/literature/ingestion.py#LiteratureIngestionService
related:
  - src/genesis_evidence/literature/connectors/base.py
  - src/genesis_evidence/literature/connectors/core.py
  - src/genesis_evidence/literature/connectors/doaj.py
  - src/genesis_evidence/literature/connectors/europe_pmc.py
  - src/genesis_evidence/literature/downloader.py
  - src/genesis_evidence/literature/http.py
  - src/genesis_evidence/literature/jats.py
  - src/genesis_evidence/literature/grobid.py
  - src/genesis_evidence/literature/evidence.py
  - src/genesis_evidence/literature/extraction_worker.py
  - src/genesis_evidence/reports/extraction_worker.py
  - src/genesis_evidence/portal/index.html
---
# literature-ingestion

把一个主题的检索流跑成一批已落库的论文与全文。

## 不变量

- 同一论文可在多个检索流中出现，应用层按 DOI/PMID/PMCID 归并为一条知识。
- 全文只有在许可证允许时才下载；拿不到全文的论文仍然是可审核的题录，不是丢弃项。
- 撤稿状态在入库时即写入，不等抽取阶段才发现。
