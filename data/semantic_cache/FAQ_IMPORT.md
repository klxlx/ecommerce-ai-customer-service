# FAQ 100 条导入与意图复核

- 导入时间：2026-11-21
- 清单状态：100/100 `approved`
- 导入命令：`python -m scripts.seed_semantic_cache --file data/semantic_cache/faq_seed_100.csv`
- 导入结果：`imported=100`
- 数据库复核：`semantic_cache` 共 100 行，其中 policy 48、product 52；`hit_count` 初始为 0。
- 真实接口复核：静态 FAQ “签收后多久可以申请无理由退货？”返回 `cached=true`、`intent=policy_knowledge`；动态订单查询返回 `cached=false`、`intent=order`，未走缓存。
- 测试：`pytest tests/unit -q` 189 passed；`pytest tests/integration -q` 152 passed。
- 缓存命中会重置 `chit_chat_streak`，避免上一轮寒暄计数残留。
- 缓存查询复核：100 条原句 `total=100, hits=100, misses=0, blocked=0`。
- `should_lookup` 复核：100/100 通过；动态/个人化表达仍被拦截。

## 2026-11-21 运行时复核（数据集回答可见性）

- 主库再次复核：`semantic_cache` 共 100 行，policy 48、product 52；此前文档声称已导入但主库为 0，已重新执行导入命令恢复。
- 缓存命中的回答统一增加语料风格开头：普通问题用“亲，您好～”，退款/退货/售后类用“亲，您先别着急，我帮您确认一下～”。
- 新增 `query_variants` 常见改述归一：`退款多久到账？` → 命中“退货退款多久到账？”；`这款耳机蓝牙版本是多少？` → 去掉“这款”后命中“耳机蓝牙版本是多少？”。
- 真实接口复核：上述两问均返回 `cached=true`；`签收后多久可以申请无理由退货？` 也返回 `cached=true`，答案均带“亲”开头。
- 情绪化业务问法也强制先安抚：`烦死了，你们这个耳机蓝牙版本到底是多少？` 返回 `intent=product_knowledge`，答案以“非常抱歉给您带来不好的体验，您先别着急。”开头；SSE 流式输出无 `replace` 事件。
- 威胁表达优先走安全分支：`我想打人` 和带业务词的 `再不退款我就打人` 均返回 `intent=deescalation`、`handoff=null`，不创建工单，也不出现“服务需求太多”。
- 测试：`pytest tests/unit -q` 189 passed；`pytest tests/integration -q` 152 passed。

## 直接过路由的意图分布（绕过缓存，仅用于诊断）

| 实际意图 | 条数 |
|---|---:|
| policy_knowledge | 31 |
| product_knowledge | 60 |
| order | 5 |
| after_sale | 3 |
| handoff | 1 |
| chit_chat | 0 |

- 100 条中没有任何一条被判为 `chit_chat`。
- 按“政策类 FAQ 应进 policy_knowledge、商品类 FAQ 应进 product_knowledge”的严格口径，有 17 条不一致；但其中多数是“退款到哪个账户、保修流程、物流查询”等操作性政策问题，路由到 after_sale/order/product_knowledge 在业务上并不错误，只是不完全是知识问答。
- 逐条路由结果未随仓库保留；如需复核，可用 `scripts.eval_agent_dataset` 重新生成。

## 与 `CHIT_CHAT_LIMIT` 的关系

- 这 100 条原句在进入图之前已 100% 命中语义缓存，缓存分支直接返回答案，不经过 `CHIT_CHAT_LIMIT=2` 的闲聊计数器。
- 即使换一种说法未命中缓存，直接过路由也没有出现 `chit_chat`；更可能进入 order/after_sale/product_knowledge/handoff，触发追问订单号或转人工，而不是闲聊限制。
- 只有真正无业务信息、连续两次以上的寒暄/闲聊，才会触发闲聊限制。
