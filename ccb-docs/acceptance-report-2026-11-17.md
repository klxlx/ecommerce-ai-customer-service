# 电商客服验收报告

日期：2026-11-17
决定：已通过

## 独立验证

- 后端：`.venv\Scripts\python.exe -m pytest -q` -> 81 passed，21 warnings。
- 前端单元测试：`npm test` -> 3 files passed，7 tests passed。
- 前端生产构建：`npm run build` -> TypeScript 和 Vite 构建通过。
- 评估：`MOCK_PROVIDERS=1 python -m scripts.evaluate` -> 5/5 个场景通过。
- 使用文档中 `uvicorn --reload` 的浏览器测试：12/12 个 Playwright 测试通过。
- 向量 schema：唯一的向量列是 `knowledge_chunks.embedding`。
- 父级绑定：`knowledge_chunks.parent_id` 为 NOT NULL，孤儿数量为 0。
- Compose 配置：`docker compose -f compose.yaml config --quiet` 通过。
- 源码检查：订单 repository 按已认证的 `user_id` 过滤；retrieval
  执行 child recall、rerank、parent expansion 和去重。

## 未通过的验收项

### 1. Windows 普通本地启动

在不使用 `--reload` 的情况下启动
`.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1
--port 8000` 会启动 HTTP 服务器，但登录请求失败并报错：

`psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode.`

在 `app/main.py` 中设置 Windows 事件循环策略的操作发生在 Uvicorn 已经创建
事件循环之后。当前的临时方案 `--reload` 不能作为文档所述本地运行路径或
演示启动器的可接受基础。

要求的行为：一个已记录的、直接的本地命令可在不依赖 `--reload` 的情况下
启动应用，并且自动化启动冒烟测试可针对该命令验证健康状态和成功登录。

### 2. 移动端顾客工作区不可用

在 iPhone 13 视口下，会话栏占用了大部分屏幕。聊天面板被压缩；会话标题被
裁切；composer placeholder 垂直换行并被明显截断。

当前的 Playwright 断言仅检查 `scrollWidth <= clientWidth`，因此无法检测该
故障。

要求的行为：顾客移动端屏幕应呈现可用的全宽聊天工作区，会话导航应通过明确
的控件或抽屉提供，而不是通过永久分屏压缩提供。

### 3. 移动端管理员工单队列不可用

在 iPhone 13 视口下，表格单元格会折叠成垂直的单字符行。summary、status、
user 和 timestamp 列在技术上仍然存在，但无法操作或阅读。

要求的行为：工单列表应呈现为有明确用途的移动端展示形式，例如带水平
label/value 对的堆叠行，或具有稳定最小列宽且可水平滚动的表格。它必须可读、
可用，且不会产生页面级水平溢出。

## 限定范围的返工

1. 添加本地启动器，在 Uvicorn 创建其事件循环之前选择 Windows selector
   事件循环。更新本地运行说明以使用该启动器。添加无 reload 的启动冒烟测试，
   检查健康状态和登录。
2. 重新设计宽度不超过 720px 的顾客移动端工作区。保持聊天全宽，提供会话
   抽屉或切换控件，并确保标题、textarea、suggestions、sources 和发送控件
   在无裁切的情况下适配。
3. 为移动端重新设计管理员工单队列。保留桌面端表格密度，同时用可读的移动端
   布局替换当前折叠表格。
4. 加强 Playwright 移动端断言，对 `/app` 和 `/admin/handoffs` 检查元素可见性、
   边界框、textarea client width/scroll width 以及页面级溢出。添加有代表性的
   截图。
5. 使用变更文件、确切命令、实际输出以及任何仍未验证的项目更新
   `ccb-docs/implementation-report.md`。

## 确认良好的内容

除非限定范围的变更需要，否则不要重写或扩展以下内容：

- PostgreSQL + PGVector schema 和父子关系；
- child recall、rerank、parent expansion、source formatting 和去重；
- 仅使用 SQL 的动态数据边界；
- 按已认证用户范围查询订单；
- 聊天持久化、greeting guard、chit-chat limit、clarification 和 handoff；
- 用户/管理员角色分离和 API 授权；
- 桌面端顾客/管理员展示。

## 后续返工复验

限定范围的返工已于 2026-11-17 独立重新测试。

- 不使用 reload 的本地启动器：
  `python -m scripts.serve --host 127.0.0.1 --port 8000`
  返回健康状态 `ok`、数据库 `ok`，并且顾客登录成功。
- 后端：`.venv\Scripts\python.exe -m pytest -q` -> 82 passed。
- 前端单元测试：`npm test` -> 3 files passed，7 tests passed。
- 前端生产构建：`npm run build` -> TypeScript 和 Vite 通过。
- 浏览器：针对 `scripts.serve` 运行 `npx playwright test` -> 12 passed，
  2 个仅移动端测试的桌面端运行被有意跳过。
- 顾客移动端截图确认：全宽聊天工作区、可用抽屉、可读的 suggestions、
  无裁切的 composer 以及可见的 sources。
- 管理端移动端截图确认：带有可用状态和行导航的可读 label/value 工单卡片。
- Docker Compose 重新构建通过。在移除数据库主机端口暴露以避开已被占用的本地
  端口后，应用进入健康状态，顾客登录成功，且 `/app` 返回 HTTP 200。验证结束后
  已移除临时 stack 和 volume。

## 最终决定

已通过。已批准的范围和验收清单均已满足。

剩余非阻塞风险：

- 由于没有可用的付费 API 凭据，实时 OpenAI-compatible chat 和 DashScope
  Embedding/Rerank 调用仍未验证。Mock providers 覆盖了完整的应用流程。
- 浏览器测试使用由 FastAPI 提供的生产构建，而不是 Vite 开发服务器。
- 按要求，项目目录仍在父级 CCB 仓库中处于未跟踪状态；未执行 commit 或 push。
