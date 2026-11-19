# P3 第三步实施账本

项目：`C:\Users\q1525\Desktop\电商客服智能体`
日期：2026-11-18
范围：`P3-06` 至 `P3-07`

## 边界

- 只做桌面端，不新增移动端功能，不执行移动端专项。
- 不增加退款、售后状态修改、知识硬删除或其他业务写操作。
- 动态价格、库存、订单、物流和售后事实继续只来自 SQL。
- 顾客端不开放知识管理或运行日志权限。
- 当前目录不是 Git 仓库；不提交、不推送、不创建 PR。

## 完成项

- [x] P3-06 管理端日志查看与筛选
- [x] P3-07 知识新增、更新、来源、动态事实隔离和权限回归
- [x] 管理端日志筛选真实浏览器回归
- [x] 前端完整组件测试与生产构建
- [x] 后端组合回归与完整回归
- [x] 更新任务清单、实现报告和本账本

## 测试矩阵

| 行为 | 测试层级 | 通过标准 |
| --- | --- | --- |
| 日志按意图、工具、检索、售后、阈值、转接、状态、用户、会话和时间筛选 | 后端集成 | 参数正确传递，结果、分页和参数边界符合契约 |
| 日志页面展示运营事实并保留筛选分页 | 前端组件 | 来源文本、阈值、转接、状态和分页状态正确 |
| 管理员进入日志页并发起筛选 | 桌面 Playwright | 导航成功，真实 API 返回 `200`，页面无安全错误态 |
| 未登录和顾客读取日志 | 后端集成 | 分别返回 `401` 和 `403` |
| 新增手工知识可检索并返回来源 | 后端组合 | 标题、手工来源 URI、版本、校验和、章节和正文一致 |
| 更新知识后旧版本可追溯 | 后端组合 | 旧版本 retired 且旧行保留，新版本 published 且检索只返回新内容 |
| 动态事实不进入向量库 | 后端组合 | 入库失败，四类知识表零残留，SQL 事实仍存在 |
| 顾客不能管理知识 | 后端组合 | 列表、详情、新增分别拒绝 `401` 和 `403` |

## 实际验证

后端 P3 日志、知识和 RAG 组合回归：

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

桌面回归运行时：

- 数据库：PostgreSQL `5433` / `cs_demo`。
- 迁移：`0003 -> 0004` 已执行到 head。
- 应用：`127.0.0.1:8000`，`MOCK_PROVIDERS=1`。
- 健康检查：`status=ok`、`database=ok`、Mock chat/embedding 可用。
- 两个跳过项均为 mobile-only 既有用例；临时应用进程已在回归后停止。

## 决策记录

- `Ruling: 不新增知识删除接口，以更新后的 retired 版本和保留旧行证明可追溯。` 原因：用户当前要求的是知识与运营回归，既有系统没有删除契约；擅自增加删除会扩大为新的写能力。若判断有误，影响是后续若要求物理删除，需要新增保留期、审计和向量清理设计。
- `Ruling: 日志页生产代码不改，只修测试定位并补浏览器链路。` 原因：现有页面和 API 已覆盖全部必需筛选，组件失败来自测试定位与查询断言，不是产品行为缺失。若判断有误，影响是仍需根据真实运营反馈补充新的展示字段。
- `Ruling: 浏览器验收只跑 desktop 项目。` 原因：用户已明确不做移动端；mobile-only 用例继续保留但不计入本轮门槛。若判断有误，影响是移动端布局回归需要单独执行。

## 已知边界

- Python 3.14 `asyncio`、`pytest-asyncio` 配置和 Alembic `path_separator` 产生弃用警告，但没有测试失败。
- 日志未增加导出、跨实例归档或长期存储能力。
- 知识更新保留旧版本，不提供硬删除接口。
- 未执行移动端专项。
- 未提交、未推送、未创建 PR。
