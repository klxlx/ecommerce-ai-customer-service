# 知识库数据

本目录保存知识库的清单、商品/规则 Markdown 和 PDF 来源元数据。

- `pdf_sources.json`：PDF 来源清单，`pdf_root` 指向 `data/knowledge/pdf_sources/`。
- `pdf_sources/`：大体量 PDF 原始文件，只在本地保留，已由 `.gitignore` 排除。
- `manifest.json`：知识库发布/版本清单。
- `policies/`、`products/`：可直接提交的小型演示知识文档。

首次部署或重建知识库前，请把 `pdf_sources.json` 中列出的 PDF 放到
`data/knowledge/pdf_sources/`，或使用 `SEED_INDEX_KNOWLEDGE=0` 跳过 PDF 索引。
