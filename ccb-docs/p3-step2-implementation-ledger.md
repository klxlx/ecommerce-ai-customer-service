# P3 第二步实施账本

项目：`C:\Users\q1525\Desktop\电商客服智能体`
日期：2026-11-18
范围：`P3-05` 管理端添加知识

## 边界

- 只完成管理端知识新增、更新、商品/政策类型选择、入库状态展示和管理员权限边界。
- 不进入 `P3-06`，不修改日志筛选业务；日志页已知失败留到第三步。
- 不进入 `P3-07`，运营端到端检索和动态 SQL 数据隔离组合回归仍未开始。
- 不做移动端专项。
- 不增加退款、售后状态修改、订单修改或其他业务写操作。
- 当前目录不是 Git 仓库；不提交、不推送、不创建 PR。

## 完成清单

- [x] 管理端支持添加或更新知识内容
- [x] 支持选择商品知识或政策知识
- [x] 支持触发入库并显示进度、成功数和失败原因
- [x] 不开放顾客端知识管理权限
- [x] 正式 PDF 知识保持只读，手工接口不能覆盖
- [x] 入库失败且未生成知识条目时展示真实失败原因
- [x] 聚焦前端、聚焦后端和生产构建验证
- [x] 更新任务清单与中文实施报告

## 已定设计

1. 手工知识使用 `manual-<uuid>` 作为新来源键；更新时沿用已有 `source_key`，不创建另一条逻辑文档。
2. 每次内容、标题、类型、适用范围或生效日期变化都形成新版本；内容与版本配置完全一致时返回 `unchanged`，不重复发布。
3. 新版本失败时保留原 published 版本继续服务，并向管理员返回截断后的安全失败原因。
4. 正式 PDF 通过已有非手工版本识别；手工接口尝试使用其 `source_key` 更新时返回 `409`，管理端列表显示只读。
5. 管理端只展示发布状态、当前版本、片段数、入库任务和可编辑性；知识详情接口只对手工来源返回可编辑内容。
6. 所有知识管理路由复用管理员依赖，未登录返回 `401`，顾客 token 返回 `403`。
7. 页面先显示同步请求进行中状态，再按接口结果展示成功计数、未变化或失败原因；失败结果不依赖 `item` 是否创建。

## 测试矩阵

| 行为 | 测试层级 | 关键断言 |
| --- | --- | --- |
| 新增商品或政策知识 | 后端集成 + 前端组件 | 两类 `kb_type` 均可入库，页面显示成功计数和片段数 |
| 更新手工知识 | 后端集成 + 前端组件 | 同一 `source_key` 发布 v2，旧版本退役，详情可回填 |
| 相同内容重复保存 | 后端集成 | 返回 `unchanged`，不增加版本 |
| 动态字段入库失败 | 后端集成 + 前端组件 | 返回安全原因，保留旧 published 版本，无堆栈和连接信息 |
| 失败且 `item=null` | 前端组件 | 展示真实失败原因，不误报通用“保存失败” |
| 正式 PDF 冲突保护 | 后端集成 + 前端组件 | `409`；标题、版本和当前版本不变；列表显示只读 |
| 管理页导航 | 前端组件 | 真实 `/admin/*` 路由下可进入知识库和运行日志 |
| 未登录/顾客 token | 后端集成 | 知识读写分别返回 `401` 和 `403` |

## 故障与根因记录

### 空知识条目的失败结果

- 现象：入库失败并返回 `item: null` 时，回调先访问 `value.item.id`，触发异常，页面进入 mutation error，掩盖后端返回的真实失败原因。
- 修复：`KnowledgePage` 先保存 `const savedItem = value.item`，仅在条目存在时更新缓存和编辑来源键。
- 回归：新增“failed ingestion result when no knowledge item was created”，断言真实动态字段原因可见，且不出现通用“保存失败”。

### 正式 PDF 手工覆盖

- 风险：管理员知道正式 PDF 的 `source_key` 后，可能尝试通过手工 upsert 绕过 PDF 注册表。
- 防护：服务层检查目标文档是否已有手工版本；没有手工版本即视为注册表管理内容并返回 `409`。
- 回归：先建立 published PDF 文档和版本，再调用手工接口；断言响应为 `409`，版本数、当前版本、标题和发布状态不变。

### AdminApp 路由测试无限更新

- 现象：直接渲染 `AdminApp` 时，嵌套路由的 `Navigate` 在缺少外层路由上下文时进入重复更新。
- 修复：测试使用 `<Route path="/admin/*" element={<AdminApp />} />`，与真实应用结构一致。
- 结果：管理员导航测试不再卡死。

### 跨测试文件 DOM 污染

- 现象：合并运行 `KnowledgePage.test.tsx` 与 `AdminApp.test.tsx` 时一度出现 `7 passed, 1 failed`，前一个用例的 DOM 影响后续查询。
- 根因：该 Vitest 环境中 Testing Library 自动清理不可靠。
- 修复：`frontend/src/test/setup.ts` 显式注册 `afterEach(() => cleanup())`。
- 结果：合并聚焦测试恢复为 `4 files passed, 11 tests passed`，完整组件测试中的其余页面也未出现该污染。

## 最终验证

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
→ tsc --noEmit passed
→ Vite built 1653 modules in 1.66s
→ dist/assets/index-FAt3-87D.js 351.81 kB, gzip 107.76 kB
```

前端完整组件测试：

```powershell
cd frontend
npm test -- --run
→ 7 files passed, 1 file failed
→ 25 tests passed, 2 tests failed
```

## 遗留状态

- 完整组件测试的两个失败均在 `LogsPage.test.tsx`，属于尚未进入的 `P3-06`：
  - 来源标题和页码位于同一 `<li>` 的多个文本节点，精确整句查询失败。
  - 下一页测试仍显示 `1-20 / 45`，需在第三步核对翻页状态和异步请求时序。
- 本步没有修改日志业务，也没有把这两个失败伪装为通过。
- `P3-07` 的端到端检索、版本追溯、动态 SQL 隔离和知识/日志权限组合回归尚未开始。
- 未执行移动端专项，未提交、推送或创建 PR。
