# 知识库数据

本目录保存知识库的清单、商品/规则 Markdown 和 PDF 来源元数据。

- `pdf_sources.json`：PDF 来源清单，`pdf_root` 指向 `data/knowledge/pdf_sources/`。
- `pdf_sources/`：大体量 PDF 原始文件，只在本地保留，已由 `.gitignore` 排除。
- `demo_sources.json`：随仓库提交的 8 份小型演示知识清单，默认启动时索引。
- `manifest.json`：历史 Markdown 文档清单；当前标记为 `legacy`，不会被默认索引。
- `policies/`、`products/`：可直接提交的小型演示知识文档。

Docker 默认只索引 `demo_sources.json`，不要求 PDF。需要完整 PDF 知识时，把
`pdf_sources.json` 中列出的 PDF 放到 `data/knowledge/pdf_sources/`，并设置
`SEED_INDEX_KNOWLEDGE=1`；缺少 PDF 只打印警告，不会阻断应用启动。也可以单独运行
`python -m scripts.index_knowledge`，按严格模式检查 PDF 索引错误。
