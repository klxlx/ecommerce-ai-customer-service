# 售后只读能力 P0 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有认证、订单和 LangGraph 主链路上增加只读售后事实查询与确定性退款阈值处理，不办理任何售后写操作。

**Architecture:** PostgreSQL 新增 `after_sales` 事实表，归属通过 `orders.user_id` 推导；仓储和工具只接收服务端认证 `user_id`。`after_sale` 意图进入独立节点，节点读取订单状态与售后记录，用代码判断新退款金额是否严格超过 `Decimal("500.00")`。历史售后查询不参与阈值判断。

**Tech Stack:** Python 3.14、FastAPI、LangGraph、SQLAlchemy 2、Alembic、PostgreSQL 16、PGVector、pytest。

**Spec:** `docs/superpowers/specs/2026-11-17-ecommerce-customer-service-design.md`

## Global Constraints

- 只实施 `P0-01` 至 `P0-15`，不启动 `P1-P3` 生产代码。
- `after_sales` 不存 `user_id`；归属只能通过 `orders.user_id` 推导。
- 模型槽位不能覆盖认证 `user_id`。
- `REFUND_REVIEW_THRESHOLD` 固定为 `Decimal("500.00")`，严格大于才转人工。
- 只有新退款诉求参与阈值判断；查询历史售后进度不得误转人工。
- “我要退款”不得再直接命中 `_HANDOFF_PATTERN`。
- 不提供退款、退货、换货或审批写操作。
- 不修改 PDF、不删除旧知识库、不修改前端、不新增基础设施。
- 不提交、不推送、不创建 PR、不改写历史。
- 正常测试使用 fake/mock provider，不调用付费服务。

## Review Focus

1. 越权订单和不存在订单必须返回完全相同的结果，不能通过响应时间以外的业务文案枚举。
2. `[]` 必须表示自有订单无售后记录，`None` 必须表示订单不存在或无权访问。
3. “我要退款”必须进入 `after_sale`，不能继续直接转人工。
4. 仅查询历史售后进度时，即使历史退款金额大于 `500.00`，也不得因阈值转人工。
5. 新退款金额大于 `500.00` 必须由代码强制转人工，等于 `500.00` 不触发。

---

### Task 1: 售后 ORM 与守卫迁移

**Files:**
- Modify: `app/db/models.py`
- Create: `migrations/versions/0002_after_sales.py`
- Modify: `tests/integration/test_migrations.py`

**Interfaces:**
- Produces: `AfterSale` ORM 模型，表名 `after_sales`。
- Produces: `after_sales.order_id` 外键和索引，字段集合固定为
  `id/order_id/service_type/status/refund_amount/reason/created_at/updated_at`。
- Produces: Alembic revision `0002`，`down_revision = "0001"`。

- [ ] **Step 1: 写失败测试**

在 `tests/integration/test_migrations.py` 中加入 `after_sales` 到必需表集合，并新增：

```python
async def test_after_sales_has_no_user_id_and_uses_order_ownership():
    columns = ...
    assert "user_id" not in columns
    assert "order_id" in columns
```

新增迁移测试，必须隔离清理或在 `finally` 中恢复到 `head`，依次验证：

- 空库执行 `alembic upgrade head` 后表存在；
- 从 `base` 升级到 `0001` 时，当前 `Base.metadata.create_all()` 已可能创建
  `after_sales`；继续升级 `head` 时 `0002` 必须跳过重复建表且成功；
- 数据库版本为 `0001` 但表被显式删除时，升级 `head` 必须重建表和索引；
- 表已完整存在时再次执行 `alembic upgrade head` 不报错；
- `after_sales.order_id` 存在索引。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py -v`

Expected: FAIL，因为模型和 `0002` 尚不存在。

- [ ] **Step 3: 实现 ORM 和幂等迁移**

`AfterSale` 使用 `TimestampMixin`，不添加关系字段到 `Order` 也能满足本阶段。
迁移必须用 `sqlalchemy.inspect(op.get_bind())` 检查：

- 表是否存在；
- 表存在时必需列是否齐全；
- `order_id` 索引是否存在。

表不存在时创建表和索引；表完整时跳过；表存在但列不完整时抛出明确异常，
不得静默变更或重复建表。

- [ ] **Step 4: 运行迁移测试**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py -v`

Expected: PASS。

### Task 2: 售后 Schema、授权仓储和工具

**Files:**
- Create: `app/schemas/after_sales.py`
- Create: `app/db/repositories/after_sales.py`
- Create: `app/tools/after_sales.py`
- Modify: `tests/db.py`
- Modify: `tests/helpers.py`
- Create: `tests/integration/test_after_sales_authorization.py`

**Interfaces:**
- Produces: `AfterSaleFact`，字段为
  `id/service_type/status/refund_amount/reason/created_at/updated_at`。
- Produces:

```python
async def lookup_after_sales(
    session: AsyncSession,
    user_id: int,
    order_no: str,
) -> list[AfterSaleFact] | None:
```

- Produces: `create_after_sale(session, order_id, ...)` 测试辅助函数。
- Consumes: Task 1 的 `AfterSale` 模型。

- [ ] **Step 1: 写失败测试**

覆盖：

- 自有订单有记录时返回列表；
- 自有订单无记录时返回空列表；
- 不存在订单返回 `None`；
- 他人订单返回 `None`；
- 返回字段不含 `user_id`；
- 售后工具把认证 `user_id` 原样传给仓储。

`tests/db.py` 的 `TABLES` 必须在 `orders` 前加入 `after_sales`。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_after_sales_authorization.py -v`

Expected: FAIL，因为 schema、仓储和工具不存在。

- [ ] **Step 3: 实现最小仓储和工具**

仓储先按 `Order.order_no == order_no` 且 `Order.user_id == user_id` 查订单：

- 查不到直接返回 `None`；
- 查到后按 `created_at`、`id` 排序查询售后记录；
- 使用 `AfterSaleFact.model_validate(row, from_attributes=True)` 或等价方式映射；
- 工具只做委托，不额外接收模型槽位中的用户字段。

- [ ] **Step 4: 运行聚焦测试**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_after_sales_authorization.py -v`

Expected: PASS。

### Task 3: 幂等演示售后种子

**Files:**
- Modify: `scripts/seed_demo.py`
- Create: `tests/integration/test_demo_seed.py`

**Interfaces:**
- Produces: `_seed_orders(...) -> dict[str, Order]`，供售后种子定位订单。
- Produces: `_seed_after_sales(session, orders) -> None`。
- Consumes: Task 1 的 `AfterSale` 模型。

- [ ] **Step 1: 写失败测试**

测试连续执行两次种子逻辑后：

- `ORD-20261110-0015` 恰好有一条演示售后记录；
- 服务类型、状态、退款金额和原因符合种子定义；
- 第二次执行后总数仍为 1。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_demo_seed.py -v`

Expected: FAIL，因为售后种子不存在。

- [ ] **Step 3: 实现幂等种子**

订单种子改为返回按订单号索引的 `dict`。售后种子通过订单号、服务类型和原因
查找已有记录；存在则跳过，不存在则插入并提交。

- [ ] **Step 4: 运行种子测试**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_demo_seed.py -v`

Expected: PASS。

### Task 4: `after_sale` 意图、状态与 Mock 路由

**Files:**
- Modify: `app/graph/state.py`
- Modify: `app/graph/router.py`
- Modify: `app/prompts/router.py`
- Modify: `app/providers/mock.py`
- Modify: `tests/unit/test_graph_routing.py`

**Interfaces:**
- Produces: `INTENTS` 包含 `after_sale`。
- Produces: `VALID_INTENTS` 包含 `after_sale`。
- Produces: `is_after_sale_progress_query(text: str) -> bool`。
- Produces: `is_new_after_sale_request(text: str) -> bool`。
- Guarantees: `is_explicit_handoff("我要退款") is False`。

- [ ] **Step 1: 写失败测试**

覆盖：

```python
assert is_new_after_sale_request("我要退款") is True
assert is_after_sale_progress_query("我要退款") is False
assert is_explicit_handoff("我要退款") is False
assert is_after_sale_progress_query("我的订单 ORD-1 退款到哪了") is True
assert is_new_after_sale_request("我要退掉订单 ORD-1") is True
assert is_after_sale_progress_query("七天无理由退货规则是什么") is False
```

另测路由可以解析 `{"intent": "after_sale", "slots": {...}}`，
以及 MockChat 对“我要退款”和“退款进度”返回 `after_sale`，
对“七天无理由规则”仍返回 `policy_knowledge`。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_graph_routing.py -v`

Expected: FAIL，因为 `after_sale` 和分类辅助函数不存在。

- [ ] **Step 3: 实现意图和确定性分类**

从 `_HANDOFF_PATTERN` 删除“我要退款”。新增意图提示，明确：

- 具体订单的售后查询或诉求走 `after_sale`；
- 通用售后、退款、退货政策走 `policy_knowledge`；
- 提取 `order_no`、`refund_amount`、`after_sale_action`。

分类函数优先识别明确的进度/状态/到账查询，再识别新申请表达；
不能只凭出现“退款”二字判断为新诉求。

- [ ] **Step 4: 运行路由测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_graph_routing.py -v`

Expected: PASS。

### Task 5: 售后节点和确定性规则

**Files:**
- Modify: `app/graph/nodes.py`
- Modify: `tests/unit/test_graph_guards.py`

**Interfaces:**
- Produces: `async def after_sale_node(state: AgentState) -> dict`。
- Produces: 常量 `REFUND_REVIEW_THRESHOLD = Decimal("500.00")`。
- Consumes: Task 2 的 `lookup_after_sales`、现有
  `get_order_and_logistics`、Task 4 的分类辅助函数。

- [ ] **Step 1: 写失败测试**

至少覆盖：

- 缺订单号时澄清；
- 不存在/无权订单返回统一“未查询到该订单”；
- 有售后记录时回答包含类型、状态、金额、原因和申请时间；
- 自有订单无记录时说明无记录并给保守指引；
- `待发货`/`运输中` 新退货请求不出现“可以直接退货退款”；
- 新退款 `500.00` 不触发阈值；
- 新退款 `500.01` 触发转人工；
- 未提供金额时使用订单实付金额；
- 仅查询历史售后进度且历史退款 `899.00` 时不触发阈值；
- 仓储或订单工具异常时用户文案不含 `db down`，并转人工；
- `tool_evidence` 包含售后事实、订单状态和阈值判断。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_graph_guards.py -v`

Expected: FAIL，因为 `after_sale_node` 不存在。

- [ ] **Step 3: 实现节点**

节点必须先调用认证归属下的订单和售后查询。金额解析顺序固定为：

1. 合法 `slots["refund_amount"]`；
2. 用户消息中的明确金额；
3. 订单实付金额。

阈值只在新退款诉求时计算。历史查询即使事实金额超阈值，也只展示事实。
异常使用通用回答并创建 `handoff`，内部异常只写服务端日志。

- [ ] **Step 4: 运行守卫测试**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_graph_guards.py -v`

Expected: PASS。

### Task 6: 图接线、trace 和工具日志

**Files:**
- Modify: `app/graph/builder.py`
- Modify: `app/services/chat.py`
- Modify: `tests/integration/test_chat_api.py`

**Interfaces:**
- Produces: `after_sale -> after_sale_node -> END` 图分支。
- Produces: `_tool_name(...)` 在工具证据含 `after_sale` 时返回 `"after_sale"`。
- Guarantees: `agent_runs.trace` 持久化售后意图、只读事实、阈值判断和转人工原因。

- [ ] **Step 1: 写失败集成测试**

新增聊天 API 测试：

- 自有订单售后查询返回售后记录；
- AgentRun trace 的 `tool_evidence.after_sale` 有记录；
- AgentRun trace 的 `tool_evidence.threshold.applied` 为 `false`；
- ToolCallLog 的 `tool_name == "after_sale"`；
- 新退款超阈值返回 `handoff`，trace 记录 `exceeded=True` 和原因；
- 历史退款 `899.00` 的进度查询不创建工单。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_chat_api.py -v`

Expected: FAIL，因为图没有售后分支或 trace 不完整。

- [ ] **Step 3: 接线并持久化证据**

给图注册 `after_sale_node` 和条件边。聊天服务 trace 增加
`handoff_reason`，工具名优先识别 `after_sale`。不得把原始异常字符串写入 trace。

- [ ] **Step 4: 运行集成测试**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_chat_api.py -v`

Expected: PASS。

### Task 7: 完整验证与实现报告

**Files:**
- Verify: 本项目全部 P0 相关文件
- Modify: `C:\Users\q1525\Desktop\电商客服智能体\ccb-docs\implementation-report.md`

**Interfaces:**
- Consumes: Task 1-6 的全部交付物。
- Produces: reviewer 的中文实现报告和实际测试证据。

- [ ] **Step 1: 运行聚焦回归**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_after_sales_authorization.py tests/integration/test_demo_seed.py tests/unit/test_graph_routing.py tests/unit/test_graph_guards.py tests/integration/test_chat_api.py -v
```

Expected: PASS。

- [ ] **Step 2: 运行完整后端回归**

Run: `.venv\Scripts\python.exe -m pytest -v`

Expected: 全部通过；若 PostgreSQL/PGVector 不可用，必须记录具体阻塞和替代验证，
不得宣称完整通过。

- [ ] **Step 3: 自查禁止项**

确认：

- 未修改 PDF 或旧知识库；
- 未修改前端；
- 未新增写接口；
- 未新增 Redis、Milvus、MySQL、Celery；
- 未提交、推送或创建 PR。

- [ ] **Step 4: 更新实现报告**

在 `ccb-docs/implementation-report.md` 新增本轮章节，使用中文记录：

- 改动文件；
- 行为实现；
- 逐条 `P0-01` 至 `P0-15` 结果；
- 实际测试命令和输出摘要；
- 已知限制和未验证项；
- 工作区未提交状态。

报告完成后再向 `main` 交接，不自行扩大范围。
