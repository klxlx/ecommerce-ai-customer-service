# 实现报告 — 电商智能客服系统

项目：`C:\Users\q1525\Desktop\电商客服智能体`
日期：2026-11-18
代理：reviewer（实现工程师）
状态：P0、P1、P2、P3 已全部完成；移动端专项暂停。

说明：第 1 至 7 节保留 P0/P1/P2 阶段的历史基线，其中旧 `manifest` 和 8 份 Markdown 的描述不代表 P3 当前正式入口。P3 第一步的最新状态、实现和验证证据见第 11 节。

## 1. 完成内容

按已批准的规格/计划实现的全栈客服系统：

- 基于 Python 3.14 的 FastAPI，使用 LangGraph 智能体编排与父子块 RAG。
- PostgreSQL 16 + PGVector（1024 维，HNSW cosine 索引）；数据边界已强制：
  价格/库存/订单/商品/物流仅存放在 SQL 表中并通过 SQL 工具查询，
  绝不索引进向量库。
- 知识入库（历史基线）：manifest → 父子分块（子块 `parent_id` NOT NULL）→ 向量化；检索流程为
  子块向量召回 → rerank → 父块扩展 → 去重 → 来源。
- React 19 + TypeScript + Vite SPA，按角色分离路由树：顾客 `/app` 与管理端 `/admin`，
  TanStack Query、Lucide 图标、项目自有设计令牌。
- 鉴权：scrypt 密码哈希，不透明 bearer token 仅以 SHA-256 摘要存储，
  会话过期，401/403 区分。无注册。无订单/退款写操作。
- 订单查询从已认证会话派生 `user_id` 并始终在 SQL 中过滤；
  订单不存在与无权访问返回相同的不可枚举回答。
- 确定性管控：招呼语（不调用 LLM）、闲聊最多 2 轮、澄清 3 次后转人工、
  显式转人工 → `handoff_tickets`。
- Mock 模型模式（`MOCK_PROVIDERS=1`）支持离线演示、测试与 Compose 冒烟。

### 顾客端（`/app`）

- 登录、会话栏（创建/切换会话）、带建议问题的聊天。
- 回答带来源引用与转人工提示；移动端安全布局。
- 已验证场景：招呼、带来源的知识问答、实时价格/库存、订单状态/物流、
  不可枚举的错误订单查询、闲聊引导、转人工。

### 管理端（`/admin`）

- 服务概览指标（会话数、消息数、用户数、工单统计）。
- 工单队列支持状态过滤；详情视图包含摘要、槽位、最近消息、
  检索/工具证据；状态流转（pending → in_progress → resolved）。
- 非管理员用户重定向到 `/app`；未登录用户重定向到 `/login`。
- 侧边栏导航在移动端保留可访问名称（标签视觉隐藏时使用 aria-label）。

## 2. 关键文件

后端（`app/`）：
- `main.py` — 应用工厂、CORS、路由注册、SPA 静态托管；Windows selector
  事件循环 policy，使异步 psycopg 在事件循环启动前导入时也能工作。
- `core/config.py`、`core/security.py` — 配置（含 `MOCK_PROVIDERS`）、scrypt/token。
- `db/models.py`、`db/session.py`、`db/repositories/*` — 14 张表、异步会话、
  auth/chat/catalog/orders/rag/handoff/admin 仓储。
- `api/deps.py`、`api/routes/{auth,chat,admin,handoffs,health}.py` — 鉴权、归属、
  角色检查、健康检查。
- `rag/{chunking,embeddings,indexer,rerank,retriever}.py` — 入库与父子块
  检索（含 rerank + 去重）。
- `tools/{catalog,orders,knowledge,handoff}.py` — 仅走 SQL 的动态工具。
- `graph/{state,nodes,router,builder,container}.py` — LangGraph 状态机。
- `services/chat.py`、`services/handoff.py`、`services/auth.py` — 编排与
  持久化（消息、智能体运行、工具日志、工单）。
- `providers/{chat,dashscope,mock}.py`、`bootstrap.py` — 提供商接线与 mock。

数据 / 脚本 / 基础设施：
- `migrations/versions/0001_initial.py`、`alembic.ini`。
- `data/knowledge/manifest.json` + 8 个 markdown 源（4 个商品、4 个规则）。
- `scripts/seed_demo.py`（users/customer + admin、4 个商品、2 个含物流的订单、
  知识索引）、`scripts/index_knowledge.py`、`scripts/evaluate.py`（5 个场景）。
- `Dockerfile`（node:20-alpine 前端构建 → python:3.14-slim 运行时托管 SPA）、
  `docker/entrypoint.sh`（等待 DB → alembic upgrade → seed_demo → uvicorn）、
  `compose.yaml`（pgvector:pg16 + app）、`.dockerignore`、`.env.example`、`README.md`、
  `docs/architecture.md`、`docs/demo-guide.md`、`requirements.txt`、`pyproject.toml`。

前端（`frontend/`）：
- `src/api/{client,auth,chat,admin}.ts`、`src/auth/{AuthContext,RequireAuth,RequireAdmin}`、
  `src/routes/{login,customer,admin}/*`、`src/styles/{tokens,app}.css`。
- `vite.config.ts`（Vitest 现在仅包含 `src/**/*.test.{ts,tsx}`，以免 Playwright 规格
  被 Vitest 收集）、`playwright.config.ts`（桌面 Chrome + iPhone 13）。
- 测试：`src/**/*.test.tsx`（7 个单元测试）、`tests/e2e/{customer,admin}.spec.ts`。

后端测试（`tests/`）：单元（安全、路由、管控、RAG 父子块、入库）、
集成（鉴权、对话、管理、动态数据、迁移、订单授权、RAG
检索/仓储）、端到端（真实 PostgreSQL 的演示流程、Compose 契约检查）。

## 3. 精确验证命令与实际结果

环境：Windows 11、`.venv` 下的 Python 3.14 虚拟环境、Node 20、PostgreSQL 16 + pgvector
容器 `cs-pg-test`，宿主机端口 5433（宿主机端口 5432 已被占用）。

后端完整测试套件（真实 PostgreSQL 16 + PGVector，数据库 `cs_test`）：

```
.venv/Scripts/python.exe -m pytest -q
→ 82 passed, 21 warnings in 18.14s
```

Seed（演示数据库 `cs_demo`）：

```
DATABASE_URL=postgresql+psycopg://cs_user:cs_password@localhost:5433/cs_demo \
MOCK_PROVIDERS=1 .venv/Scripts/python.exe -m scripts.seed_demo
→ seeded users=2 products=4 knowledge indexed=8 chunks=40 errors=0
```

评测场景（同一数据库）：

```
DATABASE_URL=...5433/cs_demo MOCK_PROVIDERS=1 python -m scripts.evaluate
→ [PASS] greeting, order_query, handoff, policy_knowledge, chit_chat_guidance
→ 5/5 scenarios passed
```

实际应用冒烟（托管构建后的 SPA）：

```
DATABASE_URL=...5433/cs_demo MOCK_PROVIDERS=1 \
  .venv/Scripts/python.exe -m scripts.serve --host 127.0.0.1 --port 8000
GET /health → {"status":"ok","database":"ok","models":{"chat":true,"embedding":true}}
GET /app    → 200
```

不使用 `--reload`。`scripts/serve.py` 在 Windows 上以显式的 selector 事件循环运行
Uvicorn 服务；自动化测试 `tests/e2e/test_local_startup.py` 以子进程方式启动这一精确
命令，并验证 `/health` 与真实登录。

前端单元测试与构建：

```
cd frontend && npm run build
→ ✓ built in 1.58s (dist/assets/index-*.js 325.10 kB, CSS 8.89 kB)
cd frontend && ./node_modules/.bin/vitest run
→ Test Files 3 passed (3), Tests 7 passed (7)
```

Playwright（桌面 Chrome + iPhone 13/WebKit）针对上述运行中的服务：

```
cd frontend && npx playwright install chromium webkit   # first run, via npmmirror host
cd frontend && npx playwright test
→ 12 passed, 2 skipped (5.4s)
```

2 个跳过项是故意限定在 mobile 项目中的测试在桌面端的运行。覆盖范围：
顾客登录 → 抽屉/新建会话 → 招呼回复；转人工状态可见；移动端聊天全宽布局，
含 bounding box、textarea client/scroll 与页面溢出断言，并附抽屉截图；
顾客被重定向离开 `/admin`；管理员登录 → 概览 → 队列；管理员打开工单详情；
移动端工单队列卡片布局含标签/值断言、视口包含与截图，随后点击行进入详情。
截图写入
`frontend/test-results/<test-id>/mobile-customer-chat.png`、
`mobile-customer-drawer.png` 与 `mobile-admin-handoffs.png`。

Docker Compose 冒烟（真实容器，不止契约测试）：

```
docker compose -p cs-smoke -f compose.yaml -f <temp override for host ports 5434/8001> build
→ Image cs-smoke-app built
docker compose -p cs-smoke ... up -d
→ db healthy; app up
docker logs cs-smoke-app-1
→ "Database is ready", alembic upgrade 0001 applied,
  "seeded users=2 products=4 knowledge indexed=8 chunks=40 errors=0"
GET http://127.0.0.1:8001/health → status ok, database ok
POST /api/auth/login (customer/password123) → token
POST /api/conversations → id 1
POST /api/conversations/1/messages "你好" → greeting intent, fixed reply
POST /api/conversations/1/messages "查询订单 ORD-20261117-0001 的物流"
  → 「运输中」, ¥299.00, latest logistics from seeded SQL data
GET /app → 200
docker compose ... down -v → stack and volume removed
```

后端 Compose 契约测试（包含在 pytest 中）：6 个测试校验 pgvector 镜像、
8000 端口暴露、启动路径中的迁移 + seed、Dockerfile 前端/pip 构建，以及
README 的 `/health` 文档 —— 全部通过。

## 4. 失败、跳过与未验证项

- 最终运行中无失败测试或跳过检查。
- 本报告最初的 P0 基线没有调用真实模型提供商；该缺口已由 P2-06 补齐，
  Chat、Embedding 和 Rerank 的真实脱敏验证结果见第 10.4 节。
- Playwright 针对 FastAPI 托管的构建产物（端口 8000）运行，而非 Vite
  dev server（5173）。Playwright 的 mobile 项目需要 WebKit（已安装）。
- Docker 基础镜像必须通过可用镜像源
  （`docker.1panel.live`）拉取并重新打 tag；本机无法解析守护进程默认镜像源
  （`docker.mirrors.ustc.edu.cn`）。Compose 冒烟仅使用临时 override 重映射宿主机端口
  （5434/8001，因为 5432 被本机原生 PostgreSQL 占用、8000 被 dev server 占用）；
  产品 compose 默认仍为 5432/8000。

## 5. 剩余风险

- 真实 DashScope 向量化/rerank 与 OpenAI 兼容对话已完成小样本连通性验证；
  长期配额、峰值吞吐、故障行为和完整检索质量仍未覆盖。
- 使用真实向量时的检索质量未验证；mock 向量是确定性的，不反映语义质量。
- 既有的桌面端"管理员打开工单详情"测试在队列为空时仍使用条件
  点击；新的移动端队列测试会先创建自己的工单，
  因此移动端呈现始终被覆盖。
- 未执行提交、推送、PR 或历史重写。项目目录在父仓库
  `D:\.GitHub` 内处于未跟踪状态。

## 6. 验收限定返工（2026-11-17 后续）

改动文件：

- `scripts/serve.py`（新增）— 本地启动器；在 Windows 上以显式
  `asyncio.SelectorEventLoop` 运行 Uvicorn（Uvicorn 0.54 会传入显式 loop factory，
  因此仅设置事件循环 policy 无效）。
- `tests/e2e/test_local_startup.py`（新增）— 以子进程启动 `python -m scripts.serve`，
  `MOCK_PROVIDERS=1`，不用 `--reload`；断言 `/health` 为 `ok`、database
  为 `ok`，且真实登录返回 token。
- `frontend/src/routes/customer/CustomerApp.tsx` — 会话栏改为
  移动端抽屉（`打开会话列表` 切换按钮、遮罩、关闭按钮、账号/退出区块）；
  ≤720px 时隐藏桌面侧边栏，聊天全宽；聊天头部始终
  渲染且标题不截断；textarea/发送/建议布局加固。
- `frontend/src/styles/app.css` — ≤720px 的抽屉/全宽/切换样式；textarea
  `min-width: 0` 与不收缩的发送按钮；管理端表格在移动端变为带
  `data-label` 行的堆叠卡片；桌面表格不变。
- `frontend/src/routes/admin/HandoffsPage.tsx` — 为移动端卡片布局增加 `data-label`
  属性（编号/摘要/状态/用户/创建时间）。
- `frontend/tests/e2e/helpers.ts`（新增）— 共享登录、感知抽屉的会话
  创建、视口/溢出断言。
- `frontend/tests/e2e/customer.spec.ts` — 移动端布局测试：抽屉开/关、聊天
  面板占满视口宽度、头部 scrollWidth ≤ clientWidth、建议/textarea/发送
  bounding box 位于视口内、textarea client/scroll 宽高、来源 chips
  适配、页面溢出、聊天与抽屉截图。
- `frontend/tests/e2e/admin.spec.ts` — 移动端队列测试：通过
  顾客流程创建工单，然后校验堆叠标签（`::before` content）、摘要宽度、
  状态胶囊宽度、所有单元格位于视口内、无页面溢出、截图，以及
  点击行进入详情页。
- `README.md`、`docs/demo-guide.md` — 本地运行命令现使用
  `python -m scripts.serve --host 127.0.0.1 --port 8000`（无 `--reload`）。
- `ccb-docs/implementation-report.md` — 本次更新。

验证命令与实际结果：

```
.venv/Scripts/python.exe -m pytest -q
→ 82 passed, 21 warnings in 18.14s   (includes test_local_startup)

DATABASE_URL=postgresql+psycopg://cs_user:cs_password@localhost:5433/cs_demo \
MOCK_PROVIDERS=1 .venv/Scripts/python.exe -m scripts.serve --host 127.0.0.1 --port 8000
GET /health → {"status":"ok","database":"ok","models":{"chat":true,"embedding":true}}
(no --reload; login tested by the pytest startup smoke and Playwright)

cd frontend && npm run build
→ ✓ built (dist/assets/index-CijeTlcz.css 10.82 kB, index-DqIX8ZRb.js 326.95 kB)
cd frontend && npm test
→ 3 files passed, 7 tests passed
cd frontend && npx playwright test
→ 12 passed, 2 skipped (mobile-only tests skipped on desktop; 5.4s)
   screenshots: mobile-customer-chat.png, mobile-customer-drawer.png,
   mobile-admin-handoffs.png under frontend/test-results/
```

验收标准映射：

1. 直接无 reload 启动器 + 自动化 health/login 冒烟 — `scripts/serve.py`、
   `tests/e2e/test_local_startup.py`（通过）。
2. 顾客端 ≤720px 抽屉 + 全宽聊天，头部/textarea/建议/
   发送不截断 — 组件/CSS 返工加 bounding box 与 textarea 指标断言。
3. 管理端工单队列移动端卡片布局，同时保留桌面表格 — `data-label`
   单元格加仅媒体查询的卡片样式。
4. 强化针对 `/app` 与
   `/admin/handoffs` 的移动端 Playwright 断言与截图 — 已实现并通过。
5. 文档与报告已更新改动文件、命令与结果。

返工后提供商连通性由 P2-06 补验，见第 10.4 节；移动端测试针对
FastAPI 托管的构建产物运行，而非 Vite dev server。

## 7. 报告路径

`C:\Users\q1525\Desktop\电商客服智能体\ccb-docs\implementation-report.md`

## 8. P0 只读售后能力实施结果（2026-11-17）

### 8.1 实施结论与边界

P0-01 至 P0-15 已完成。售后能力只读取订单与售后事实，不创建或修改退款、退货、换货、订单等业务状态；允许写入的范围仅为聊天消息、Agent trace、工具调用日志和转人工工单。`user_id` 始终来自认证上下文，不接受模型槽位覆盖；不存在订单与无权订单使用同一不可枚举响应。

本次没有修改前端，没有实施 PDF 入库、知识库管理、日志筛选、登录限流、依赖降级或其他 P1-P3 功能。当前目录不是 Git 仓库，未执行提交、推送或创建 PR。

### 8.2 主要改动

新增：

- `app/schemas/after_sales.py`
- `app/db/repositories/after_sales.py`
- `app/tools/after_sales.py`
- `tests/integration/test_after_sales_authorization.py`
- `tests/integration/test_demo_seed.py`

修改：

- `tests/integration/test_migrations.py`
- `tests/helpers.py`
- `scripts/seed_demo.py`
- `app/graph/state.py`
- `app/graph/router.py`
- `app/graph/nodes.py`
- `app/graph/builder.py`
- `app/prompts/router.py`
- `app/providers/mock.py`
- `app/services/chat.py`
- `tests/unit/test_graph_routing.py`
- `tests/unit/test_graph_guards.py`
- `tests/integration/test_chat_api.py`

### 8.3 P0-01 至 P0-15 逐项结果

| 项目 | 结果 |
| --- | --- |
| P0-01 | 通过。`after_sales` 表、ORM 与 `0002` 守卫迁移可用；测试覆盖空库升级到 `head`、`0001` 已建表、`0001` 后表被删除、重复升级补齐索引，以及表结构不完整时明确失败。 |
| P0-02 | 通过。`after_sales` 不保存 `user_id`，归属通过 `order_id -> orders.user_id` 推导。 |
| P0-03 | 通过。仓储和工具显式接收服务端认证的 `user_id`，模型槽位不能覆盖。 |
| P0-04 | 通过。不存在订单与无权订单都返回同一 `None` 语义，并生成相同不可枚举答复。 |
| P0-05 | 通过。路由支持 `after_sale`，具体订单售后与通用售后政策可区分。 |
| P0-06 | 通过。“我要退款”确定性进入 `after_sale`，不再直接转人工。 |
| P0-07 | 通过。售后进度展示类型、状态、退款金额、原因和申请时间，并按创建时间与 ID 稳定排序。 |
| P0-08 | 通过。自有订单无售后记录时如实说明，并给出查看订单售后入口或转人工核实的保守指引。 |
| P0-09 | 通过。“待发货”和“运输中”不承诺直接退货退款，改为提示查看拦截、拒收或人工核实。 |
| P0-10 | 通过。仅新退款请求金额严格大于 `Decimal("500.00")` 时由代码转人工；`500.00` 不转人工，`500.01` 转人工。金额优先级为有效槽位金额、消息明确金额、订单实付金额。 |
| P0-11 | 通过。仅查询已有售后进度时阈值不应用，历史退款金额 `899.00` 不会触发误转人工。 |
| P0-12 | 通过。售后订单查询或售后记录查询异常只返回脱敏提示并转人工，内部错误不进入答复。 |
| P0-13 | 通过。演示种子新增订单 `ORD-20261110-0015` 的售后记录，重复运行不会重复插入。 |
| P0-14 | 通过。专项与完整后端测试均通过；trace 记录售后意图、只读事实、订单状态、阈值是否应用、请求金额、阈值、是否超限和转人工原因，工具调用日志记为 `after_sale`。 |
| P0-15 | 通过。本节记录改动、行为、命令、实际结果、限制与未验证项。 |

### 8.4 验证命令与实际结果

聚焦回归：

```powershell
.venv\Scripts\python.exe -m pytest tests\integration\test_migrations.py tests\integration\test_after_sales_authorization.py tests\integration\test_demo_seed.py tests\unit\test_graph_routing.py tests\unit\test_graph_guards.py tests\integration\test_chat_api.py -v
→ 56 passed, 41 warnings in 9.15s
```

完整后端回归：

```powershell
.venv\Scripts\python.exe -m pytest -v
→ 112 passed, 42 warnings in 21.62s
```

保留警告均为项目已知弃用提示，包括 pytest-asyncio 未显式设置 fixture loop scope、Python 3.14 对 Windows selector event loop policy 的弃用提示，以及 Alembic `path_separator` 旧配置回退提示，不影响本次测试结果。

### 8.5 已知限制与未验证项

- P0 仅提供售后事实查询和新退款审查转人工，不实际创建或推进退款、退货、换货及订单状态。
- 前端未修改，顾客端售后展示、管理端售后视图和对应移动端页面不在本次范围。
- P0 阶段完整测试使用 Mock/Fake Provider；真实外部聊天、Embedding 和 Reranker
  的连通性已由 P2-06 补验，长期配额、峰值吞吐和故障行为仍不在 P0 验证范围。
- 登录限流、依赖降级、PDF 入库、知识库管理、日志筛选均未实施。
- P1 和 P2 后续已完成，分别见第 9、10 节；P3 尚未开始，不属于本次交付。

## 9. P1 交互体验实施结果（2026-11-17）

### 9.1 实施结论与边界

P1-01 至 P1-05 已完成。P1 只把 P0 已具备的售后只读事实呈现给顾客和管理员，没有新增退款、退货、换货、审批、订单状态修改或售后写接口。售后事实仍来自 SQL，不进入知识库。

本轮按用户确认的范围停止在 P1，没有实施 P2 的登录防护、限流、Provider 超时与降级、指标和异常分类，也没有实施 P3 的 PDF 入库、知识库管理、日志查看与筛选。没有新增移动端功能；既有移动端测试保留为回归，但不作为 P1 验收门槛。当前目录不是 Git 仓库，未执行提交、推送或创建 PR。

### 9.2 主要改动

后端与迁移：

- `app/schemas/chat.py` — 聊天响应增加结构化售后上下文，覆盖 `has_records`、`no_records`、`not_available`、`query_error` 四种状态，并保留原 `answer` 文本。
- `app/db/models.py`、`app/db/repositories/chat.py` — 聊天消息持久化售后上下文快照。
- `migrations/versions/0003_chat_after_sale_context.py` — 为聊天消息增加可空售后快照列，支持历史消息恢复相同展示。
- `app/graph/state.py`、`app/graph/nodes.py`、`app/services/chat.py` — 将售后事实和异常状态传入响应与消息持久化。
- `tests/helpers.py`、`tests/integration/test_chat_api.py`、`tests/integration/test_migrations.py`、`tests/unit/test_graph_guards.py` — 覆盖四种状态、历史恢复、异常脱敏、阈值场景和迁移结果。

前端：

- `frontend/src/api/chat.ts`、`frontend/src/api/admin.ts` — 增加售后状态、记录、订单与阈值事实类型。
- `frontend/src/routes/customer/CustomerApp.tsx` — 在对话中增加只读 `AfterSalePanel`，展示服务类型、状态、退款金额、原因、申请时间和订单状态。
- `frontend/src/routes/admin/afterSaleEvidence.ts` — 仅从安全 evidence 白名单读取订单号、订单状态、实付金额、售后记录和阈值事实。
- `frontend/src/routes/admin/HandoffDetailPage.tsx` — 在工单详情增加“关联订单与售后”区域，移除原始 evidence JSON，避免展示内部工具输出。
- `frontend/src/routes/admin/HandoffsPage.tsx` — 带售后上下文的工单显示“售后”标识。
- `frontend/src/styles/app.css` — 增加顾客售后事实、管理端售后事实、阈值和队列标识样式。
- `frontend/src/routes/customer/MessageBubble.test.tsx`、`frontend/src/routes/admin/HandoffDetailPage.test.tsx`、`frontend/src/routes/admin/HandoffsPage.test.tsx` — 增加组件回归。
- `frontend/tests/e2e/helpers.ts`、`frontend/tests/e2e/customer.spec.ts`、`frontend/tests/e2e/admin.spec.ts` — 增加桌面端售后展示、空状态、不可见状态、超阈值转人工和管理端安全上下文回归。

### 9.3 P1-01 至 P1-05 逐项结果

| 项目 | 结果 |
| --- | --- |
| P1-01 | 通过。API 返回订单号、订单状态、售后记录、阈值事实和明确状态；无记录、不可见、查询异常可区分；原文本回答保持兼容；动态数据未写入知识库。 |
| P1-02 | 通过。顾客对话新增售后结构化区域，展示服务类型、处理状态、退款金额、原因、申请时间；无记录显示保守指引；查询异常显示脱敏提示并标明人工转接。 |
| P1-03 | 通过。管理员工单详情展示关联订单与只读售后事实；工单队列显示“售后”标识；原始 evidence JSON 不再展示；顾客 token 仍受管理员权限边界限制。 |
| P1-04 | 通过。`has_records`、`no_records`、`not_available`、`query_error` 使用统一文案；不存在订单与无权订单均为 `not_available`；内部异常、SQL 和堆栈不会进入响应或管理端安全区域。 |
| P1-05 | 通过。后端 API、顾客端组件、管理端组件和桌面端 Playwright 回归均通过；移动端专项未新增，也未列为 P1 门槛。 |

### 9.4 验证命令与实际结果

前后端聚焦回归：

```powershell
cd frontend
npm test -- src/routes/customer/MessageBubble.test.tsx src/routes/admin/HandoffDetailPage.test.tsx src/routes/admin/HandoffsPage.test.tsx
→ 3 files passed, 10 tests passed
```

后端完整回归：

```powershell
.venv\Scripts\python.exe -m pytest -v
→ 116 passed, 42 warnings in 22.34s
```

前端完整组件回归与构建：

```powershell
cd frontend
npm test
→ 5 files passed, 14 tests passed

npm run build
→ TypeScript check passed; Vite build completed
```

桌面端浏览器回归：

```powershell
cd frontend
npx playwright test --project=desktop
→ 9 passed, 2 skipped (mobile-only tests), 4.1s
```

本地运行验证使用 `MOCK_PROVIDERS=1` 和端口 `5433` 的 PostgreSQL，
`GET /health` 返回数据库与 Mock 模型均可用；真实 Provider 连通性已由
P2-06 在第 10.4 节单独补验。

### 9.5 已知限制与后续阶段

- P1 只读展示不改变业务数据，不提供退款、退货、换货或审批操作。
- 移动端专项暂停；P1 不新增移动端功能，桌面端为本次验收门槛，既有移动端回归仍可单独执行。
- 本节记录 P1 交付时的状态；P2 已于 2026-11-18 完成，详见第 10 节。
- P3 尚未实施：PDF 审计与入库、知识库管理、日志查看和筛选均待后续阶段。
- P1 验证使用 Mock/Fake Provider；P2 已另行完成真实聊天、Embedding 和 Reranker 的脱敏验证。

## 10. P2 稳定性与安全实施结果（2026-11-18）

### 10.1 实施结论与边界

P2-01 至 P2-06 已完成。登录失败防护、聊天与管理端限流、Provider 超时重试和最终降级、集中状态与异常分类、运行指标、日志脱敏以及真实 Provider 最小验证均已落地。

本轮没有修改前端，没有执行移动端专项，没有进入 P3，没有实施 PDF 入库、知识库管理或日志查看筛选，也没有新增退款、退货、换货、审批、订单状态修改或售后写接口。动态订单、物流、价格、库存和售后事实仍只来自 SQL。当前目录不是 Git 仓库，未提交、推送或创建 PR。

### 10.2 主要改动

核心控制：

- `app/core/rate_limit.py` - 新增线程安全登录失败防护和滑动窗口限流器。
- `app/core/config.py` - 增加登录锁定、聊天/管理端限流、Provider 重试与三类超时/预算配置。
- `app/core/errors.py` - 新增 Provider 领域异常及可重试分类。
- `app/core/resilience.py` - 新增单次超时、有限重试、总耗时预算和 Provider 指标记录。
- `app/core/states.py` - 集中定义会话、意图、消息角色、工具、售后上下文、工单和 Provider 状态。
- `app/core/metrics.py` - 新增进程内计数器和延迟快照。
- `app/core/logging.py` - 新增统一日志脱敏与 `RedactingFormatter`。
- `app/main.py` - 在应用状态中装配登录防护器、两个独立限流桶、指标注册表和日志配置。

API 与 Provider：

- `app/api/routes/auth.py` - 登录加入失败窗口、临时锁定、统一不可枚举失败响应和登录指标。
- `app/api/deps.py` - 聊天按认证用户限流，管理端使用独立桶；超限返回 `429`、`X-Error-Code`、`Retry-After`。
- `app/providers/chat.py` - OpenAI 兼容 Chat 适配器接入超时、重试、领域错误映射，并修复未配置 Key 时误用占位 Client 的问题。
- `app/rag/embeddings.py`、`app/rag/rerank.py` - DashScope Embedding 与 Rerank 接入相同韧性策略和错误分类。
- `app/services/chat.py` - 记录聊天、工具、售后、阈值、人工转接和 Provider 指标；Provider 最终失败时返回脱敏答复并创建人工工单。
- `scripts/verify_providers.py` - 新增真实 Provider 脱敏验证脚本；缺少 Key 返回退出码 `2`，失败返回 `1`，全部成功返回 `0`。
- `.env.example` - 补充 P2 可配置项和说明。

测试：

- 新增 `tests/unit/test_security_controls.py`、`test_provider_resilience.py`、`test_states_and_errors.py`、`test_metrics_and_log_redaction.py`、`test_verify_providers.py`。
- 扩展 `tests/unit/test_provider_adapters.py`，并新增 `tests/integration/test_login_protection.py`、`test_rate_limits.py`、`test_provider_failures.py`、`test_chat_metrics.py`。

### 10.3 P2-01 至 P2-06 逐项结果

| 项目 | 结果 |
| --- | --- |
| P2-01 | 通过。默认失败窗口为 300 秒，同用户名与客户端组合失败 5 次后锁定 900 秒；不存在用户、密码错误和非活跃用户均返回同一 `401` 与 `AUTH_INVALID_CREDENTIALS`；锁定返回 `429`、`AUTH_TEMPORARILY_LOCKED` 和正整数 `Retry-After`；成功登录清除该键失败历史。 |
| P2-02 | 通过。聊天每个认证用户默认 20 次/60 秒；管理端独立桶默认 120 次/60 秒；两个桶互不影响；并发检查使用锁保证计数原子性；超限返回稳定错误码和 `Retry-After`。限流器为应用进程内状态。 |
| P2-03 | 通过。Chat 单次 30 秒/总预算 60 秒，Embedding 单次 20 秒/总预算 60 秒，Rerank 单次 15 秒/总预算 30 秒；默认最多重试 2 次，仅连接、限流、超时类瞬态错误重试；认证和无效请求不重试；Chat 最终失败安全转人工。 |
| P2-04 | 通过。会话、意图、消息角色、工具、售后上下文、工单与 Provider 状态集中到 `app/core/states.py`；HTTP 与 SDK 错误映射到统一领域异常，并显式区分可重试与不可重试。 |
| P2-05 | 通过。指标覆盖聊天请求/成功/失败/延迟、工具调用、售后查询、退款阈值、人工转接、Provider 请求/超时/重试/降级/预算、登录失败/锁定和限流超限；日志在统一 Formatter 中脱敏密码、token、API Key、Authorization、完整消息/内容标记、电话、地址和订单号。 |
| P2-06 | 通过。真实 Chat、Embedding、Rerank 均返回 `status=ok`；Embedding 验证为 1024 维；脚本只输出模型、延迟、维度、结果数量、顺序、分数和错误类别，不输出 Key、请求头、回答正文、文档正文或完整上游响应。 |

### 10.4 验证命令与实际结果

P2 专项回归：

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\test_security_controls.py tests\integration\test_login_protection.py tests\integration\test_rate_limits.py tests\unit\test_provider_resilience.py tests\unit\test_provider_adapters.py tests\integration\test_provider_failures.py tests\unit\test_states_and_errors.py tests\unit\test_metrics_and_log_redaction.py tests\integration\test_chat_metrics.py tests\unit\test_verify_providers.py -q
→ 40 passed, 20 warnings in 3.72s
```

后端完整回归：

```powershell
.venv\Scripts\python.exe -m pytest -q
→ 156 passed, 44 warnings in 25.77s
```

真实 Provider 脱敏验证：

```powershell
.venv\Scripts\python.exe -m scripts.verify_providers
→ exit 0
chat: status=ok, model=qwen-plus, elapsed_ms=581.661
embedding: status=ok, model=text-embedding-v3, dimensions=1024, elapsed_ms=258.117
rerank: status=ok, model=gte-rerank-v2, count=3, order=[0, 2, 1],
        scores=[0.371689, 0.005972, 0.005927], elapsed_ms=264.765
```

前端组件测试与构建：

```powershell
cd frontend
npm test -- --run
→ 5 files passed, 14 tests passed

npm run build
→ TypeScript check passed; Vite build completed
```

桌面端浏览器回归：

```powershell
cd frontend
npx playwright test --project=desktop
→ 9 passed, 2 skipped (mobile-only tests), 3.6s
```

回归前演示库执行了 `alembic upgrade head` 和 Mock 播种；应用使用 `MOCK_PROVIDERS=1`、PostgreSQL `5433` 与 `127.0.0.1:8000`。回归完成后已停止临时应用进程。

### 10.5 已知限制与未验证项

- 登录失败状态、限流桶和指标注册表都是单应用进程内状态；多进程或多实例部署不能共享计数，也未引入 Redis。
- 指标目前通过应用内 `MetricsRegistry.snapshot()` 读取，尚未提供 Prometheus 导出、外部时序存储或管理端展示。
- 真实 Provider 验证是受控的小样本连通性与契约检查，不代表长期配额、峰值吞吐、完整知识库检索质量或供应商 SLA。
- 韧性策略提供超时、有限重试和安全降级，但没有熔断器、跨进程熔断状态或后台自动恢复探测。
- 日志脱敏作为统一兜底，不替代调用点避免记录敏感明文；完整日志审计与筛选属于 P3。
- 移动端专项未执行；本轮只完成既有桌面端 Playwright 回归。
- 本节记录 P2 完成时的状态；P3 第一步随后已完成，见第 11 节。

## 11. P3 第一步：正式 PDF 版本化知识入库（2026-11-18）

### 11.1 实施结论与边界

P3-01 至 P3-04 已完成并通过第一阶段验收。10 份正式 PDF 已登记来源、校验和、页码、章节和适用范围，可由正式注册表入口提取、版本化入库、幂等更新和回溯引用。

本轮只处理后端知识数据、PDF 提取、数据库迁移、入库脚本、测试和中文验收记录。没有修改前端，没有开放知识写接口，没有实施管理端知识添加、日志查看筛选或 `P3-05` 至 `P3-07`，没有删除旧 Markdown，没有直接清空旧向量，也没有提交、推送或创建 PR。

### 11.2 主要改动

- `data/knowledge/pdf_sources.json`：登记 10 份正式 PDF；6 份商品用户指南、4 份政策规则。
- `ccb-docs/p3-legacy-knowledge-audit.md`、`data/knowledge/manifest.json`：旧 Markdown 标记为 legacy，保留文件但退出正式检索口径。
- `app/rag/pdf_extraction.py`：支持华为指南大纲提取和京东规则章节/条款提取，保留文件、校验和、页码和章节路径。
- `app/rag/indexer.py`：新增正式 PDF 注册表入口，支持单份失败后继续、幂等跳过、版本发布、失败标记和重试。
- `app/db/models.py`、`migrations/versions/0004_versioned_knowledge.py`：增加逻辑文档、文件版本、入库任务及 parent/chunk 版本绑定。
- `app/rag/retriever.py`、`app/db/repositories/rag.py`、`app/schemas/knowledge.py`：检索只读取当前 published 版本，引用返回版本、文件、校验和、页码和章节路径。
- `scripts/index_knowledge.py`、`scripts/seed_demo.py`、`scripts/evaluate.py`：正式入口全部切换到 PDF 注册表。
- `tests/unit/test_pdf_knowledge_sources.py`、`test_pdf_extraction.py`、`tests/integration/test_rag_repository.py`、`test_rag_retrieval.py`：覆盖来源审计、10 份 PDF 提取、幂等、版本更新、失败保留旧版本、检索隔离和全量入库。

### 11.3 验收结果

| 任务 | 结果 |
| --- | --- |
| P3-01 | 通过。8 份旧 Markdown 均有校验和并标记为 `legacy`；未删除文件，也未直接清空旧向量。 |
| P3-02 | 通过。10 份正式 PDF 均有唯一来源编号、文件名、主题、SHA-256、类型和适用范围。 |
| P3-03 | 通过。真实 PDF 可形成带有效 parent 的 child 分块，页码、章节、文件和校验和可追溯；向量维度校验为 1024。 |
| P3-04 | 通过。相同版本配置幂等跳过；新校验和发布新版本并退役旧版本；失败时保留旧的 published 版本；失败任务可重试。 |
| 检索回归 | 通过。legacy、draft、failed、retired 版本不进入正式检索；引用返回正式 PDF 溯源字段。 |

### 11.4 验证命令与实际结果

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

### 11.5 限制与停止点

- 10 份真实 PDF 的全量入库验收使用 1024 维假向量；真实 DashScope Chat、Embedding 和 Rerank 的连通性已在 P2 单独验证，但本阶段没有为正式知识库消耗真实 Embedding 配额。
- 管理端添加知识、日志查看与筛选、运营回归属于 `P3-05` 至 `P3-07`，尚未开始。
- 移动端专项继续暂停。
- 已停止在 P3 第一步；未进入第二步，未提交或推送代码。

## 12. P3 第二步：管理端知识添加与更新（2026-11-18）

### 12.1 实施结论与边界

`P3-05` 已完成。管理员可以在现有管理端知识库页面新增或更新手工知识，选择商品知识或政策知识，触发同步入库，并看到处理中状态、成功计数、内容未变化提示或真实失败原因。正式 PDF 资料在管理端显示为只读，不能通过手工接口覆盖；未登录用户和顾客 token 均不能访问知识管理接口。

本轮只完成 `P3-05`。没有进入 `P3-06` 日志查看与筛选，没有修改日志页业务逻辑，没有进入 `P3-07` 运营回归，没有实施移动端专项，没有增加退款、售后状态修改或其他业务写操作。当前目录不是 Git 仓库，未提交、推送或创建 PR。

### 12.2 主要改动

后端：

- `app/api/routes/admin_knowledge.py` - 提供管理员知识列表、详情和新增/更新接口。
- `app/services/knowledge_admin.py` - 将手工内容转换为版本化知识文档，接入现有幂等入库流程，阻止手工请求覆盖正式 PDF。
- `app/db/repositories/knowledge_admin.py` - 返回发布版本、入库任务、片段数、来源类型和 `can_edit` 状态。
- `app/schemas/knowledge.py` - 增加手工知识写入、入库结果和知识详情契约。
- `app/main.py` - 注册管理端知识路由。

前端：

- `frontend/src/routes/admin/KnowledgePage.tsx` - 增加知识列表、商品/政策类型选择、新增与更新表单、入库进度及结果展示。
- `frontend/src/routes/admin/AdminApp.tsx` - 在管理端导航中挂载知识库工作区。
- `frontend/src/api/admin.ts` - 增加知识列表、详情和 upsert API。
- `frontend/src/styles/app.css` - 增加桌面端知识列表、编辑器和入库状态样式。
- `frontend/src/routes/admin/KnowledgePage.test.tsx` - 覆盖加载、入库成功、失败原因、更新、空条目失败和 PDF 只读。
- `frontend/src/routes/admin/AdminApp.test.tsx` - 在真实 `/admin/*` 路由结构下验证知识库与运行日志导航。
- `frontend/src/test/setup.ts` - 显式执行 Testing Library `cleanup()`，避免跨测试文件 DOM 污染。

后端测试：

- `tests/integration/test_admin_knowledge_api.py` - 覆盖商品/政策新增、新版本更新、幂等未变化、失败保留旧版本、详情读取、正式 PDF 冲突保护和入库结果。
- `tests/integration/test_admin_api.py` - 覆盖未登录与顾客 token 不能读取或写入知识管理接口。

### 12.3 验收结果

| P3-05 验收项 | 结果 |
| --- | --- |
| 管理端支持添加或更新知识内容 | 通过。新增生成手工来源；更新通过同一 `source_key` 发布新版本并退役旧版本。 |
| 支持选择商品知识或政策知识 | 通过。前端单选和后端参数化测试均覆盖 `product`、`policy`。 |
| 支持触发入库并显示进度、成功数和失败原因 | 通过。页面显示处理中状态、成功知识数、发布版本数、片段数、未变化提示和安全失败原因。 |
| 不开放顾客端知识管理权限 | 通过。接口拒绝未认证请求 `401` 和顾客 token `403`；前端知识页位于管理员路由树。 |
| 正式 PDF 来源保护 | 通过。正式 PDF 在列表中为只读；使用其 `source_key` 调用手工 upsert 返回 `409`，原标题、版本和发布状态不变。 |
| 空条目失败展示 | 通过。入库失败且没有返回知识条目时，页面直接显示真实失败原因，不再误报通用保存错误。 |

### 12.4 验证命令与实际结果

管理端知识相关前端聚焦回归：

```powershell
cd frontend
.\node_modules\.bin\vitest.cmd run src\routes\admin\KnowledgePage.test.tsx src\routes\admin\AdminApp.test.tsx src\routes\admin\HandoffsPage.test.tsx src\routes\admin\HandoffDetailPage.test.tsx --reporter=verbose
→ 4 files passed, 11 tests passed in 2.95s
```

知识管理与管理 API 后端聚焦回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_admin_knowledge_api.py tests\integration\test_admin_api.py -q --tb=short
→ 13 passed, 18 warnings in 5.97s
```

前端生产构建：

```powershell
cd frontend
npm run build
→ TypeScript check passed
→ Vite build completed in 1.66s
```

前端完整组件测试：

```powershell
cd frontend
npm test -- --run
→ 7 files passed, 1 file failed
→ 25 tests passed, 2 tests failed
```

完整组件测试的两个失败均属于尚未进入的 `P3-06` 日志页：

- 来源标题和页码位于同一列表项的多个文本节点，当前精确整句查询失败。
- 点击下一页后断言仍为 `1-20 / 45`；翻页状态与 API 调用时序需在 `P3-06` 处理。

这两个失败不涉及知识管理页、管理员导航或 P3-05 API；本步没有修改日志筛选业务。

### 12.5 停止点与后续

- `P3-05` 已完成；下一步是 `P3-06` 管理端日志查看与筛选。
- 日志页已有两个组件测试失败，应在 `P3-06` 中按业务要求修复，不纳入本步知识管理交付。
- `P3-07` 尚未开始，新增知识的端到端检索来源验证、旧知识更新追溯、动态 SQL 数据不进向量库和权限组合回归仍留在第三步。
- 未执行移动端专项，未提交或推送代码。

## 13. P3 第三步：日志运营与知识回归（2026-11-18）

### 13.1 实施结论与边界

`P3-06` 和 `P3-07` 已完成，P3 三步全部完成。管理端可查看并按意图、工具、检索结果、售后事实、阈值判断、人工转接、运行状态、用户、会话和时间范围筛选运行日志；知识新增、更新、来源追溯、版本退役、动态事实隔离和管理权限均有组合验收。

本轮没有修改日志页或知识页生产业务逻辑。后端日志接口和页面能力已经在既有实现中满足要求，本轮修复了日志页组件测试的定位与分页时序断言，并补充管理端日志真实浏览器回归、知识运营组合验收和中文记录。没有执行移动端专项，没有增加知识删除、退款、售后状态修改或其他业务写操作，没有提交、推送或创建 PR。

### 13.2 主要改动

- `frontend/src/routes/admin/LogsPage.test.tsx` - 修正来源文本跨节点定位和下一页查询断言；覆盖筛选参数、分页保留筛选、空状态和安全错误状态。
- `frontend/tests/e2e/admin.spec.ts` - 新增仅限桌面端的管理员运行日志回归，验证导航、筛选表单和真实 `/api/admin/logs?intent=knowledge` 请求。
- `tests/integration/test_admin_knowledge_api.py` - 新增 4 条运营组合验收：新增知识可检索并返回可追溯来源；更新后旧行保留、旧版本退役且检索只返回新版本；动态 SQL 事实被拒绝且知识表无残留；知识接口拒绝未登录和顾客请求。
- `ccb-docs/p3-step3-implementation-ledger.md` - 记录第三步范围、测试矩阵、实际命令结果、决策和未验证边界。
- `ccb-docs/p1-p3-task-list.md`、`ccb-docs/implementation-report.md` - 更新 P3 全部完成状态和第三步验收证据。

### 13.3 验收结果

| 任务 | 结果 |
| --- | --- |
| P3-06 日志查看与筛选 | 通过。日志页展示意图、工具、检索来源、售后事实、阈值、人工转接、状态、用户和会话；支持对应筛选、时间范围、分页、空状态和安全错误状态。 |
| P3-06 日志权限 | 通过。日志接口拒绝未登录请求 `401` 和顾客 token `403`；管理员桌面浏览器可进入页面并完成筛选请求。 |
| P3-07 新增知识检索 | 通过。管理端新增政策知识后可被真实检索管线召回；标题、手工来源 URI、版本、校验和、章节和正文均匹配。 |
| P3-07 更新可追溯 | 通过。同一 `source_key` 更新后旧版本为 `retired`、新版本为 `published`，旧 parent/chunk 仍保留，正式检索只返回新内容。当前没有知识硬删除接口，因此以版本退役和旧行保留验证“更新后可追溯”。 |
| P3-07 动态事实隔离 | 通过。含动态价格、库存、订单号、物流和售后事实的手工知识入库失败；`KnowledgeDocument`、`KnowledgeDocumentVersion`、`KnowledgeParent`、`KnowledgeChunk` 均零残留，原 SQL 业务事实仍可读取。 |
| P3-07 知识权限 | 通过。知识列表、详情和新增均拒绝未登录 `401` 和顾客 token `403`。 |

### 13.4 验证命令与实际结果

P3-06/P3-07 后端组合回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_admin_logs_api.py tests\integration\test_admin_knowledge_api.py tests\integration\test_rag_repository.py tests\integration\test_rag_retrieval.py -q --tb=short
→ 35 passed, 18 warnings in 180.34s
```

后端完整回归：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short
→ 194 passed, 54 warnings in 211.52s
```

前端完整组件测试：

```powershell
Push-Location frontend
.\node_modules\.bin\vitest.cmd run
Pop-Location
→ 8 files passed, 27 tests passed in 4.28s
```

前端生产构建：

```powershell
Push-Location frontend
npm run build
Pop-Location
→ TypeScript check passed
→ Vite build completed in 1.72s
```

桌面端真实浏览器回归：

```powershell
Push-Location frontend
.\node_modules\.bin\playwright.cmd test --project=desktop
Pop-Location
→ 11 passed, 2 skipped in 3.7s
```

两个跳过项都是显式限定为 mobile 项目的既有用例；本轮按要求不做移动端专项。浏览器回归使用 `MOCK_PROVIDERS=1`、PostgreSQL `5433` 的 `cs_demo` 数据库和 `127.0.0.1:8000`，`/health` 返回 `status=ok`、`database=ok`。回归前演示库迁移执行到 `0004`，回归后临时应用进程已停止。

### 13.5 已知边界

- 后端警告为 Python 3.14 `asyncio` 弃用提示、`pytest-asyncio` 配置提示和 Alembic `path_separator` 提示；本轮没有失败或功能异常。
- 当前没有知识硬删除接口。“旧知识删除或更新后可追溯”只验证更新路径：旧版本退役但父子块保留，正式检索不再返回旧内容。若后续要求物理删除，需要另行定义保留期、审计和向量清理规则。
- 日志筛选依赖现有单应用数据库中的运行记录和工具调用记录；没有新增跨实例日志存储、导出或长期归档。
- 移动端专项继续暂停；本轮浏览器验收只以桌面端为门槛。
- 当前目录不是 Git 仓库，未提交、未推送、未创建 PR。
