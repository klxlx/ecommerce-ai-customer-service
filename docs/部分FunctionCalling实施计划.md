# 部分 Function Calling 实施计划与现状

## 1. 结论

本次采用“只读、单点、带开关”的 Function Calling 试点，不全面 FC 化，不做写操作 FC。

- FC 默认关闭；关闭时订单意图仍走原 `order_node`。
- 打开后只有订单/物流只读意图进入 FC，模型只能在 `order_list`、`order_detail` 中选择并填写订单号。
- `user_id` 由认证会话注入，模型无法传入或覆盖。
- 工具调用全部经过 `execute_tool` runtime：schema 校验、只读 allowlist、超时、一次重试、每工具熔断、结构化 `tool_evidence`。
- 模型只负责“选工具 + 填参数”；最终回答使用订单业务数据和语气指南生成，再由现有 sentinel 复核。任何规划失败、工具失败、空结果或空回答都回退到确定性 `order_node`。
- 静态 FAQ 仍走 `semantic_cache`；动态订单/物流查询被 `_BLOCKED` 挡住，不读缓存。两者不冲突。

## 2. 请求链路

```text
route
  └─ intent=order 且 FC_READONLY_ENABLED=1
       └─ order_fc
            ├─ tool_planner：模型选择 allowlist 工具
            ├─ execute_tool：校验 + 超时/重试/熔断 + tool_evidence
            ├─ build_order_tool_answer_messages：仅依据业务数据生成回答
            └─ 失败/空结果 → order_node（原确定性模板）
```

关闭开关时链路保持原样：

```text
route → order_node → list_owned_orders / get_order_and_logistics
```

## 3. 工具契约

| 工具 | 参数 | 语义 | 数据源 |
|---|---|---|---|
| `order_list` | 无 | 当前登录用户全部订单 | `orders` |
| `order_detail` | `order_no` | 当前登录用户单个订单及物流 | `orders` |

- `order_no` 必须匹配订单号格式；不得从历史猜测或编造。
- 两个工具均为 `read_only=True`；写操作工具无法注册进本试点。
- 未注册工具、身份覆盖参数、额外参数、缺失参数都返回结构化失败，不执行。
- 仓储对缺失和越权订单统一返回 `None`，FC 路径同样不枚举订单是否存在。

## 4. Runtime 策略

代码位置：

- `app/tools/registry.py`：工具定义、JSON schema、allowlist。
- `app/tools/runtime.py`：参数校验、只读检查、超时、重试、熔断、evidence。
- `app/graph/tool_planner.py`：最多 `FC_MAX_STEPS` 步的受控规划循环。
- `app/graph/nodes.py::order_fc_node`：FC 节点与确定性回退。
- `app/providers/chat.py::complete_with_tools`：原生 tool-calling 适配。

默认策略：

- 单工具超时 8 秒。
- 连接类失败重试 1 次，退避 0.2 秒；参数/权限/身份错误不重试。
- 同一工具连续失败 3 次后熔断 30 秒。
- 每次调用记录 `tool.call.total`、`tool.failure.total`、`tool.latency_ms`、`tool.circuit_open.total`。
- 数据库读取使用 savepoint 包裹，失败回滚该次工具事务。

## 5. 开关

```env
FC_READONLY_ENABLED=0
FC_MAX_STEPS=2
FC_ALLOWED_TOOLS=order_list,order_detail
FC_PLANNER_MODEL=
```

- `FC_READONLY_ENABLED=0` 是默认值，也是回滚开关。
- `FC_PLANNER_MODEL` 留空时跟随 `ROUTER_MODEL`。
- `FC_MAX_STEPS` 限制模型最多发起两次工具调用；超过即停止并进入回答或回退。

## 6. 回答与事实优先级

1. 安全合规要求。
2. 工具返回的订单业务数据。
3. 静态 FAQ / 知识库（仅在不与工具数据冲突时）。
4. 数据集语气指南，只提供表达风格，不提供事实。

FC 回答 Prompt 明确要求只使用订单业务数据中出现的订单号、状态、金额、时间、商品和物流；物流为空时只说暂无更新，不猜到达时间。模型回答为空或含知识缺口标记时回退确定性模板。

## 7. 已落地与验收

已落地：

- `complete_with_tools` provider 适配与 tool_calls 解析。
- 工具 registry、runtime、allowlist、schema 校验、超时/重试/熔断。
- `order_fc` 节点、planner prompt、订单回答 prompt。
- 图路由：`intent=order` 且开关打开时进入 `order_fc`。
- `AgentRun.trace["fc"]` 记录步数、工具调用、参数、状态和 fallback 原因；`tool_evidence` 记录业务数据。
- 30 条订单 FC 离线评测集：`tests/fixtures/fc_order_eval.json`。
- 评测脚本：`scripts/eval_fc_order.py`。

实测：

- 30 条评测：工具选择 30/30（100%），参数 20/20（100%）。
- 单元测试：207 passed。
- 集成测试：159 passed。
- 覆盖场景：订单列表、订单详情、无工具调用回退、越权订单不枚举、provider tool schema 传递、参数非法、重试、熔断、超时。

## 8. 本次不做

- 退款、取消、改地址、建工单、发券等写操作 FC。
- 用 FC 替换静态 FAQ 或语义缓存。
- 引入 LangGraph `ToolNode`、tool loop 图或 checkpointer。
- 新增数据库迁移。
- 商品价格库存、知识检索、天气、本地时间的 FC 化。
- 把现有所有确定性节点迁移到 runtime；本次 runtime 只服务 FC 路径，后续再逐工具迁移。

## 9. 回滚

将 `FC_READONLY_ENABLED` 设为 `0`，订单意图立即回到原 `order_node`，无需数据库回滚。FC 代码和测试保留，不影响原有路径。
