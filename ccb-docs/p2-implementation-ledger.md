# P2 实施账本

项目：`C:\Users\q1525\Desktop\电商客服智能体`
开始日期：2026-11-18
范围：`p1-p3-task-list.md` 中 P2-01 至 P2-06

## 实施约束

- 只改后端、测试、验证脚本和项目文档。
- 不做移动端专项，不新增前端功能。
- 不实施 P3 的 PDF 入库、知识库管理或日志筛选。
- 不新增退款、退货、换货、审批或订单写操作。
- 不引入 Redis、Milvus、MySQL、Celery 等基础设施。
- 当前目录不是 Git 仓库；不提交、不推送、不创建 PR。
- 真实 Provider 验证只记录模型、延迟、错误类别、向量维度、rerank 数量和顺序，不记录 Key、请求头或完整响应。

## 已定方案

1. 登录失败状态、聊天限流器和管理端限流器保存在 `app.state`，每个应用实例独立。
2. 登录防护使用失败窗口和临时锁定；用户不存在、密码错误和非活跃用户保持同一失败响应。
3. 聊天限流按认证用户 ID，管理端使用完全独立的限流桶；超限返回 `429`、`X-Error-Code` 和 `Retry-After`。
4. Chat、Embedding、Rerank 使用明确单次超时、有限重试和总耗时预算，仅重试瞬态故障。
5. Provider 错误统一映射为领域异常；聊天路径最终失败时返回脱敏提示并创建人工工单。
6. 意图、消息角色、工具、售后上下文、工单和 Provider 状态集中定义，字符串值保持兼容。
7. 使用进程内指标注册表记录聊天、限流、工具、售后、阈值、转人工和 Provider 稳定性指标。
8. 日志在输出前统一脱敏密码、token、API Key、完整消息全文和订单隐私字段。

## 执行清单

- [x] P2-01 登录失败防护与测试
- [x] P2-02 聊天/API 限流与并发测试
- [x] P2-03 Provider 超时、重试、预算和降级
- [x] P2-04 集中状态与异常分类
- [x] P2-05 指标与日志脱敏
- [x] P2-06 真实 Provider 最小验证
- [x] 后端、前端组件、构建和桌面浏览器回归
- [x] 更新任务清单、项目简报和实现报告
- [x] 文档状态一致性检查与旧口径清理

## TDD 记录

| 阶段 | 测试 | RED 结果 | GREEN 结果 |
| --- | --- | --- | --- |
| P2-01 | `test_security_controls.py`、`test_login_protection.py` | 2026-11-18：`app.core.rate_limit` 不存在，测试在收集阶段失败 | 通过。登录窗口、锁定、不可枚举响应、清除失败历史和有效期边界均由对应测试覆盖，纳入 P2 专项 `40 passed`。 |
| P2-02 | `test_security_controls.py`、`test_rate_limits.py` | 2026-11-18：限流器不存在；应用工厂尚未注册独立限流状态 | 通过。并发原子性、窗口过期、聊天用户隔离、管理端独立桶和稳定 `429` 响应均由对应测试覆盖，纳入 P2 专项 `40 passed`。 |
| P2-03 | `test_provider_resilience.py`、`test_provider_adapters.py`、`test_provider_failures.py` | 2026-11-18：`app.core.errors` 和 `app.core.resilience` 不存在，测试在收集阶段失败 | 通过。瞬态重试、不重试错误、单次超时、总预算、三类 Provider 适配器、指标和聊天最终转人工均由对应测试覆盖，纳入 P2 专项 `40 passed`。 |
| P2-04 | `test_states_and_errors.py` | 2026-11-18：`app.core.states` 不存在，测试在收集阶段失败 | 通过。状态字符串兼容、HTTP 错误映射和网络错误可重试分类通过，纳入 P2 专项 `40 passed`。 |
| P2-05 | `test_metrics_and_log_redaction.py`、`test_chat_metrics.py` | 2026-11-18：指标注册表、`RedactingFormatter` 和 `redact_sensitive` 不存在 | 通过。指标快照、聊天/工具/售后/阈值/转人工指标和日志脱敏均通过，纳入 P2 专项 `40 passed`。 |
| P2-06 | `test_verify_providers.py` | 2026-11-18：验证脚本及脚本契约测试尚不存在，测试在收集阶段失败 | 通过。缺少 Key 不调用 Provider、失败只暴露领域类别、成功只输出安全元数据；3 个合约测试通过，真实检查退出码 `0`。 |

## 最终验证

2026-11-18 新鲜结果：

- P2 专项：`40 passed, 20 warnings in 3.72s`。
- 后端完整测试：`156 passed, 44 warnings in 25.77s`。
- 真实 Provider 验证：退出码 `0`。
  - Chat：`qwen-plus`，`581.661 ms`。
  - Embedding：`text-embedding-v3`，`1024` 维，`258.117 ms`。
  - Rerank：`gte-rerank-v2`，3 条，顺序 `[0, 2, 1]`，`264.765 ms`。
- 前端组件：`5 files passed, 14 tests passed`。
- 前端构建：`npm run build` 通过。
- 桌面端 Playwright：`9 passed, 2 skipped in 3.6s`；跳过项为移动端专项。
- 临时应用使用 `MOCK_PROVIDERS=1`、PostgreSQL `5433` 和 `127.0.0.1:8000`，回归后已停止。
- 任务清单状态：P1 与 P2 全部勾选完成，P3 全部保持未勾选。

未包含在 P2 范围：

- 移动端专项。
- P3 的 PDF 入库、知识库管理、日志查看与筛选。
- Redis、多实例共享限流、Prometheus 导出或熔断器。
- 前端功能修改、代码提交、推送或 PR。
