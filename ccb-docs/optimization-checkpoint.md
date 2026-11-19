# CCB 优化检查点

日期：2026-11-17

## 当前项目

- 项目根目录：`C:\Users\q1525\Desktop\电商客服智能体`
- 顾客端：`/app`
- 管理端：`/admin`
- 项目实现报告：`ccb-docs/implementation-report.md`
- 验收报告：`ccb-docs/acceptance-report.md`
- 当前优化前模型：reviewer 使用 `deepseek-v4-pro`
- 已停止当前长时间运行的 reviewer 任务

## 已完成的近期工作

- 独立无 reload 本地启动器与 health/login 冒烟测试已完成。
- 顾客端移动抽屉与全宽聊天布局返工已完成。
- 管理端移动端工单卡片布局返工已完成。
- 已加入针对 `/app` 和 `/admin/handoffs` 的移动端 Playwright 断言与截图。
- 实现报告和验收报告已翻译为中文。

## 未完成工作

- P0-A 售后事实表与守卫迁移尚未通过验收。
- `migrations/versions/0002_after_sales.py` 已存在，但尚未纳入完整测试和验收。
- `tests/integration/test_migrations.py` 尚未完成 P0-A 要求的测试编辑。
- P0-A 的唯一任务书：`ccb-docs/reviewer-task-p0-a.md`

## 恢复顺序

1. 两个 Agent 使用全新会话，不加载旧聊天历史。
2. `reviewer` 使用 `deepseek-flash`。
3. 只处理 `ccb-docs/reviewer-task-p0-a.md`。
4. 先检查现有 `0002_after_sales.py`，不要直接视为通过。
5. 按任务书允许的最小文件范围完成 ORM、迁移和测试。
6. 运行：

   ```powershell
   .venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py -v
   ```

7. 在 `ccb-docs/implementation-report.md` 末尾追加 P0-A 结果。
8. 由 `main` 独立验收后决定是否继续后续 P0 子任务。

## 会话使用约束

- 禁止扫描整个仓库或读取大日志。
- 每次只读取明确指定的文件和小范围行。
- 不在同一个超长会话中反复压缩。
- 任务完成后先写报告，再清空会话。
- **每次向 reviewer 派发任务前，main 必须确认 reviewer 空闲并执行 `/clear`；
  只有同一任务必须延续上下文时才执行 `/compact`。**
- 清理工作必须在派发前完成，不能把旧对话直接带入新任务。
