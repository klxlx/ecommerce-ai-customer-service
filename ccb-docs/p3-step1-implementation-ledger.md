# P3 第一步实施账本

项目：`C:\Users\q1525\Desktop\电商客服智能体`
日期：2026-11-18
范围：`P3-00` 至 `P3-04`

## 边界

- 只处理后端知识数据、PDF 提取、迁移、入库脚本、测试和中文验收记录。
- 不修改管理端或顾客端页面，不开始 `P3-05` 至 `P3-07`。
- 不实施移动端专项。
- 不开放知识写接口，不增加售后、订单、退款或物流写操作。
- 当前目录不是 Git 仓库；不提交、不推送、不创建 PR。
- 不删除旧知识文件，不直接清空旧向量；旧资料只做审计并退出正式检索。

## 已定设计

1. 10 份正式 PDF 使用固定来源清单登记来源编号、SHA-256、主题、类型、适用范围和生效日期。
2. 华为用户指南优先使用 PDF 大纲形成章节 parent；京东规则无大纲时按章节、条款和表格标题切分。
3. parent 与 child 都保留页码、章节路径和来源版本关系；引用返回文件、页码、章节和校验和。
4. 逻辑文档、文件版本和入库任务分层建模；相同文件校验和加相同切分配置视为重复入库。
5. 新版本先构建并校验完整性和 1024 维向量，全部通过后再发布；失败时旧版本继续服务。
6. 检索只返回 `published` 的当前版本，旧 Markdown 没有正式 PDF 版本关系，因此不参与正式检索。
7. 动态价格、库存、订单、物流和售后状态只来自 SQL，不进入向量库。

## 执行清单

- [x] P3-00 验收基线、测试矩阵与真实 PDF 盘点
- [x] P3-01 旧 Markdown 审计与 legacy 标记
- [x] P3-02 10 份 PDF 来源清单
- [x] P3-03 PDF 提取、页码/章节/来源元数据与 1024 维边界
- [x] P3-04 版本化幂等入库、失败回滚和检索回归
- [x] 聚焦测试、后端完整测试、真实 10 份 PDF 提取与全量入库验证
- [x] 更新任务清单和第一阶段中文验收报告

## 测试矩阵

| 行为 | 测试层级 | 失败应变红的原因 |
| --- | --- | --- |
| 来源清单完整、唯一且校验和正确 | 单元 | 缺少正式来源或清单漂移 |
| 旧 Markdown 全部标为 legacy | 单元 | 未审计资料仍进入正式清单 |
| 华为大纲形成章节及页码范围 | 单元 | 大纲层级、页偏移或目录页处理错误 |
| 京东规则按章节/条款切分 | 单元 | 无大纲 PDF 被压成单块或丢失页码 |
| PDF 提取保留文件校验和、页码和章节路径 | 单元/真实资料 | 来源不可追溯 |
| 新表、约束和索引可由 `0004` 幂等创建 | 集成 | 迁移重复执行失败或结构缺失 |
| 相同校验和和切分配置跳过 | 集成 | 重复入库 |
| 向量数量、维度或文本完整性失败 | 集成 | 新版本发布不完整或旧版本被删除 |
| 检索只读取已发布当前版本 | 集成 | legacy、草稿或失效版本泄漏 |
| citation 返回页码、章节和校验和 | 单元/集成 | 答案来源不可追溯 |

## TDD 记录

### 来源清单与 legacy 审计

- RED：`.\.venv\Scripts\python.exe -m pytest tests\unit\test_pdf_knowledge_sources.py -q`
- RED 结果：`5 failed`。缺少 `pdf_sources.json` 和中文审计记录，旧 `manifest.json` 缺少 v2 legacy 字段。
- GREEN：`.\.venv\Scripts\python.exe -m pytest tests\unit\test_pdf_knowledge_sources.py -q`
- GREEN 结果：`5 passed`。

### PDF 提取

- RED：`.\.venv\Scripts\python.exe -m pytest tests\unit\test_pdf_extraction.py -q`
- RED 结果：`2 failed, 7 passed`。华为 PDF 顶层大纲项没有直接页码，章节容器被错误丢弃。
- GREEN：`.\.venv\Scripts\python.exe -m pytest tests\unit\test_pdf_extraction.py tests\unit\test_pdf_knowledge_sources.py -q`
- GREEN 结果：`9 passed, 17 warnings in 24.91s`。10 份正式 PDF 均有文本层，提取器验证校验和、页数、页码范围、章节路径和策略支持。

### 正式 PDF 注册表入口与版本化入库

- RED：新增入口测试先因缺少 `index_registered_pdfs` 而失败；错误来源应记录后继续处理其余正式 PDF。
- GREEN：实现注册表入口，并让 `index_knowledge.py`、`seed_demo.py`、`evaluate.py` 全部切换到该入口；旧 `index_manifest` 仅保留兼容位置，正式脚本不再调用。
- 全量验收：使用临时 PostgreSQL 数据库、正式 `pdf_sources.json`、10 份真实 PDF 和 1024 维假向量执行完整提取、切分、版本发布、parent/child 绑定和任务记录。
- 全量结果：`1 passed, 18 warnings in 132.50s`；10 个文档、10 个 published 版本、10 个 succeeded 入库任务均写入成功，错误数为 0。

## 决策记录

- `Ruling: P3 第一步按 P3-00 至 P3-04 整体交付。` 原因：用户明确要求按三步推进，且这三项共享来源模型、迁移、提取和入库接口。若判断有误，影响是第一步审查范围较大，但不扩大到管理端页面。
- `Ruling: 旧 Markdown 不删除文件，也不在迁移中强制删除已有向量。` 原因：用户要求不直接清空旧知识库；正式检索通过发布版本过滤退出旧资料。若判断有误，影响是数据库中可能短期保留不可检索的旧行。
- `Ruling: PDF 提取使用 pypdf。` 原因：10 份正式 PDF 都有文本层，pypdf 足以完成文本、大纲和页数提取，避免引入 OCR 或重型解析栈。若判断有误，影响是复杂版式需要后续增加版面解析器。
- `Dependency: pypdf==7.1.0` 已登记到 `pyproject.toml`、`requirements.in` 和 `requirements.txt`。

## 最终验证

正式 10 份 PDF 全量入库回归：

```powershell
$env:DATABASE_URL='postgresql+psycopg://cs_user:cs_password@localhost:5433/cs_test'
.\.venv\Scripts\python.exe -m pytest tests\integration\test_rag_repository.py::test_registered_pdf_entrypoint_ingests_all_official_sources -q --tb=short
→ 1 passed, 18 warnings in 132.50s
```

后端完整回归：

```powershell
$env:DATABASE_URL='postgresql+psycopg://cs_user:cs_password@localhost:5433/cs_test'
.\.venv\Scripts\python.exe -m pytest -q --tb=short
→ 172 passed, 54 warnings in 194.33s
```

旧入口检查：

```powershell
rg -n "index_manifest|MANIFEST_PATH|manifest\.json" scripts app tests --glob '!**/__pycache__/**'
→ 正式脚本 0 处；仅 app/rag/indexer.py 的兼容函数和旧资料审计测试仍引用 manifest。
```

验证边界：

- 10 份真实 PDF 的提取、切分和完整入库使用 1024 维假向量，未在本阶段重新调用真实 DashScope。
- 检索隔离、迁移、幂等、版本发布、失败保留旧版本和引用溯源均包含在上述完整后端回归中。
- 未修改前端，未开放知识写接口，未进入 `P3-05` 至 `P3-07`，未提交或推送代码。
