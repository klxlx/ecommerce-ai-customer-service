# 架构说明

## 分层结构

```text
FastAPI 路由层 (app/api)
  -> 认证与归属校验 (app/api/deps.py)
  -> 服务编排层 (app/services)
  -> LangGraph 智能体 (app/graph)
       -> 检索层 (app/rag)     —— 父子块 RAG：召回 -> 重排 -> 父块补全 -> 去重
       -> 工具层 (app/tools)   —— 商品价格库存 / 订单物流，全部走 SQL
  -> 数据层 (app/db)           —— SQLAlchemy ORM + 仓储
  -> PostgreSQL 16 + PGVector
```

## 核心数据边界

- **静态知识**：商品参数、使用方法、注意事项、保修、交易规则 → 写入 `knowledge_sources` /
  `knowledge_parents` / `knowledge_chunks`，子块带非空 `parent_id`，向量维度固定 1024。
- **动态事实**：商品价格库存、订单、订单明细、物流 → 仅存在于 `products` / `orders` /
  `order_items` / `logistics_events`，通过 SQL 工具实时查询，**绝不写入向量表**。
- 检索管线：嵌入查询 → 子块向量召回 → 重排 → 按 `parent_id` 载入父块 → 去重 → 带来源的上下文。

## 智能体编排

```text
START -> route（问候/转人工确定性判定，否则 LLM 意图路由）
      -> greeting | knowledge | catalog | order | chit_chat | clarification | handoff
      -> END
```

- 问候与短问候返回固定文案，不调用 LLM。
- 订单查询的 `user_id` 由会话服务端注入，LLM 只产出 `order_no` 槽位，SQL 恒带归属过滤，
  缺失与越权订单返回相同文案，不可枚举。
- 闲聊连续两轮后引导回业务；澄清失败、显式转人工、工具异常均落 `handoff_tickets`。

## 安全

- 密码：`hashlib.scrypt` + 每用户随机盐；会话：`secrets.token_urlsafe`，仅存 SHA-256 摘要，
  服务端过期。
- 顾客路由要求顾客或管理员会话；管理路由要求管理员角色（401 / 403 语义分离）。

## 验证策略

- 单元测试用假聊天/嵌入/重排提供器；集成测试跑真实 PostgreSQL + PGVector。
- 实时模型冒烟测试可选，由环境变量（`MOCK_PROVIDERS=0` + 真实 Key）门控。
