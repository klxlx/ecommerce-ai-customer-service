# FAQ 候选审核说明

本目录下的 `faq_candidates_100.csv` 是 A+B 方案中 B 的交付物。**清单已于 2026-11-21 审核通过，并已写入 pgvector 语义缓存。**

## 构成

- 共 100 条：政策 48 条、商品 52 条。
- 答案 `answer_draft` 全部直接摘录自 `data/knowledge/policies` 与 `data/knowledge/products` 的对应章节，没有使用合成客服 bot 的回答。
- `source_title` / `source_heading` / `source_path` 指向唯一来源章节；`source_snippet` 是该章节前 700 字，便于核对。
- 全部 `review_status=approved`、`importable=true`。

## 审核与导入结果

1. 100 条全部标记 `approved`。
2. 由 approved 行生成 `faq_seed_100.csv`（100 行，字段 `question,answer,kb_type,sources,expires_at`）。
3. 通过 `python -m scripts.seed_semantic_cache --file data/semantic_cache/faq_seed_100.csv` 导入，脚本返回 `imported=100`。
4. 数据库复核：`semantic_cache` 表共 100 行，policy 48、product 52。
5. 查询复核：100 条原句全部命中缓存，`total=100, hits=100, misses=0, blocked=0`。

## 导入边界

- 静态政策、商品知识进入 FAQ 语义缓存。
- 动态订单、物流、价格、库存、售后进度问题不进入 FAQ 缓存；`should_lookup` 仍会拦截“我的订单、订单号、快递单号、退款进度、我要退/换、投诉、转人工、多少钱、有货吗”等表达。
- 静态 FAQ 的“颜色/尺码是否有货”属于库存可选性，路由归 `price_stock`，缓存答案只覆盖稳定的规格说明，不替代实时库存接口。
